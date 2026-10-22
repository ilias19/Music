package com.ilias.practiceroom

import ai.onnxruntime.OnnxJavaType
import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicLong

/**
 * Native ONNX Runtime for the Python scanner (see python/ort_shim.py).
 *
 * Python passes raw little-endian bytes + shape + dtype name; tensors stay here
 * and Python only holds Long handles, so the decoder's KV cache is reused
 * across steps without copies. Sessions are cached per model path: homr opens
 * the models again for every page and they're 50 MB each.
 */
class OrtBridge {
    private val env: OrtEnvironment = OrtEnvironment.getEnvironment()
    private val sessions = ConcurrentHashMap<Int, OrtSession>()
    private val sessionByPath = ConcurrentHashMap<String, Int>()
    private val tensors = ConcurrentHashMap<Long, OnnxTensor>()
    private val nextTensor = AtomicLong(1)
    private val threads = Runtime.getRuntime().availableProcessors().coerceIn(1, 4)

    @Synchronized
    fun open(path: String): Int {
        sessionByPath[path]?.let { return it }
        val opts = OrtSession.SessionOptions().apply {
            setOptimizationLevel(OrtSession.SessionOptions.OptLevel.ALL_OPT)
            setIntraOpNumThreads(threads)
            // Idle worker threads sleep instead of spinning. Spinning helps a little when the app has
            // the CPU to itself, but in the background Android gives it a smaller share, and spinning
            // threads then burn that share waiting instead of working.
            addConfigEntry("session.intra_op.allow_spinning", "0")
            addConfigEntry("session.inter_op.allow_spinning", "0")
        }
        val session = env.createSession(path, opts)
        val handle = sessions.size + 1
        sessions[handle] = session
        sessionByPath[path] = handle
        return handle
    }

    fun inputNames(session: Int): Array<String> = sessions.getValue(session).inputNames.toTypedArray()

    fun outputNames(session: Int): Array<String> = sessions.getValue(session).outputNames.toTypedArray()

    fun tensor(data: ByteArray, shape: LongArray, dtype: String): Long {
        val buf = ByteBuffer.allocateDirect(data.size).order(ByteOrder.LITTLE_ENDIAN)
        buf.put(data)
        buf.rewind()
        val t = when (dtype) {
            "float32" -> OnnxTensor.createTensor(env, buf.asFloatBuffer(), shape)
            "int64" -> OnnxTensor.createTensor(env, buf.asLongBuffer(), shape)
            "int32" -> OnnxTensor.createTensor(env, buf.asIntBuffer(), shape)
            "float16" -> OnnxTensor.createTensor(env, buf, shape, OnnxJavaType.FLOAT16)
            "uint8" -> OnnxTensor.createTensor(env, buf, shape, OnnxJavaType.UINT8)
            "int8" -> OnnxTensor.createTensor(env, buf, shape, OnnxJavaType.INT8)
            "bool" -> OnnxTensor.createTensor(env, buf, shape, OnnxJavaType.BOOL)
            else -> throw IllegalArgumentException("Unsupported dtype $dtype")
        }
        return store(t)
    }

    fun run(session: Int, names: Array<String>, handles: LongArray, outNames: Array<String>): LongArray {
        val inputs = HashMap<String, OnnxTensor>(names.size)
        for (i in names.indices) inputs[names[i]] = tensors.getValue(handles[i])
        // The Result is deliberately not closed: its tensors are handed to Python,
        // which frees each one through free() when it's done with it.
        val result = sessions.getValue(session).run(inputs, outNames.toSet())
        return LongArray(outNames.size) { i ->
            val value = result.get(outNames[i]).orElseThrow {
                IllegalStateException("Model produced no output ${outNames[i]}")
            }
            store(value as OnnxTensor)
        }
    }

    fun read(handle: Long): ByteArray {
        val buf = tensors.getValue(handle).byteBuffer.order(ByteOrder.LITTLE_ENDIAN)
        val out = ByteArray(buf.remaining())
        buf.get(out)
        return out
    }

    fun shape(handle: Long): LongArray = tensors.getValue(handle).info.shape

    fun dtype(handle: Long): String = when (tensors.getValue(handle).info.type) {
        OnnxJavaType.FLOAT -> "float32"
        OnnxJavaType.FLOAT16 -> "float16"
        OnnxJavaType.INT64 -> "int64"
        OnnxJavaType.INT32 -> "int32"
        OnnxJavaType.UINT8 -> "uint8"
        OnnxJavaType.INT8 -> "int8"
        OnnxJavaType.BOOL -> "bool"
        else -> throw IllegalStateException("Unsupported output type")
    }

    fun free(handle: Long) {
        tensors.remove(handle)?.close()
    }

    fun liveTensors(): Int = tensors.size

    private fun store(t: OnnxTensor): Long {
        val h = nextTensor.getAndIncrement()
        tensors[h] = t
        return h
    }
}
