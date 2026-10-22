"""A stand-in for the `onnxruntime` Python package, backed by a native bridge.

Chaquopy has no onnxruntime wheel for Android, but Microsoft ships a native
Android build (com.microsoft.onnxruntime:onnxruntime-android). This module
implements the slice of the onnxruntime Python API that homr uses, and
forwards the actual work to a small bridge object:

    bridge.open(path) -> int                               session handle
    bridge.inputNames(session) / bridge.outputNames(session) -> list[str]
    bridge.tensor(data: bytes, shape: list[int], dtype: str) -> int   tensor handle
    bridge.run(session, in_names, in_handles, out_names) -> list[int]
    bridge.read(tensor) -> bytes          raw little-endian data
    bridge.shape(tensor) -> list[int]
    bridge.dtype(tensor) -> str
    bridge.free(tensor)

Tensors live on the native side, so homr's decoder KV-cache (outputs fed
straight back as inputs on the next step) is never copied through Python.

On Android the bridge is the Kotlin class OrtBridge; tests use MockBridge,
which implements the same calls with the real onnxruntime package.
"""
from __future__ import annotations

import sys
import types

import numpy as np

_DTYPES = {
    np.dtype("float32"): "float32",
    np.dtype("float16"): "float16",
    np.dtype("int64"): "int64",
    np.dtype("int32"): "int32",
    np.dtype("uint8"): "uint8",
    np.dtype("int8"): "int8",
    np.dtype("bool"): "bool",
}
_NP = {v: k for k, v in _DTYPES.items()}

_bridge = None


def install(bridge) -> types.ModuleType:
    """Register this module as `onnxruntime` and route it to `bridge`."""
    global _bridge
    _bridge = bridge
    mod = sys.modules[__name__]
    sys.modules["onnxruntime"] = mod
    return mod


def preload(path) -> None:
    """Load a model into the native runtime ahead of use (the bridge caches sessions by path)."""
    _bridge.open(str(path))


def _to_bytes(buf) -> bytes:
    """Java byte[] arrives as a Chaquopy jarray of *signed* bytes."""
    if isinstance(buf, (bytes, bytearray)):
        return bytes(buf)
    try:
        return memoryview(buf).cast("B").tobytes()   # fast path: buffer protocol
    except TypeError:
        return np.asarray(list(buf), dtype=np.int8).tobytes()


class OrtValue:
    """A tensor held by the bridge. Freed when the Python object goes away."""

    __slots__ = ("_h", "__weakref__")

    def __init__(self, handle: int):
        self._h = int(handle)

    @classmethod
    def ortvalue_from_numpy(cls, arr, device_type: str = "cpu", device_id: int = 0) -> "OrtValue":
        arr = np.ascontiguousarray(arr)
        dt = _DTYPES.get(arr.dtype)
        if dt is None:
            raise TypeError(f"Unsupported dtype {arr.dtype}")
        shape = [int(d) for d in arr.shape]
        return cls(_bridge.tensor(arr.tobytes(), shape, dt))

    def numpy(self):
        dt = _NP[str(_bridge.dtype(self._h))]
        shape = [int(d) for d in _bridge.shape(self._h)]
        data = _to_bytes(_bridge.read(self._h))
        return np.frombuffer(data, dtype=dt).reshape(shape).copy()

    def shape(self):
        return [int(d) for d in _bridge.shape(self._h)]

    def __del__(self):
        try:
            if _bridge is not None:
                _bridge.free(self._h)
        except Exception:
            pass


class _NodeArg:
    def __init__(self, name: str):
        self.name = name
        self.shape = None
        self.type = None


class IOBinding:
    def __init__(self, session: "InferenceSession"):
        self._s = session
        self._inputs: dict[str, OrtValue] = {}
        self._outputs: list[str] = []
        self._results: list[OrtValue] = []

    def bind_cpu_input(self, name: str, arr) -> None:
        self._inputs[name] = OrtValue.ortvalue_from_numpy(arr)

    def bind_ortvalue_input(self, name: str, value: OrtValue) -> None:
        self._inputs[name] = value

    def bind_output(self, name: str, device_type: str = "cpu", device_id: int = 0, *a, **k) -> None:
        if name not in self._outputs:
            self._outputs.append(name)

    def clear_binding_inputs(self) -> None:
        self._inputs.clear()

    def clear_binding_outputs(self) -> None:
        self._outputs.clear()

    def get_outputs(self) -> list[OrtValue]:
        return self._results

    def copy_outputs_to_cpu(self) -> list:
        return [v.numpy() for v in self._results]

    def _run(self) -> None:
        names = list(self._inputs)
        handles = [self._inputs[n]._h for n in names]
        outs = self._outputs or self._s._output_names
        res = _bridge.run(self._s._h, names, handles, list(outs))
        self._results = [OrtValue(h) for h in res]


class InferenceSession:
    def __init__(self, path, sess_options=None, providers=None, provider_options=None, **kwargs):
        self._path = str(path)
        self._h = _bridge.open(self._path)
        self._input_names = [str(n) for n in _bridge.inputNames(self._h)]
        self._output_names = [str(n) for n in _bridge.outputNames(self._h)]

    def get_inputs(self):
        return [_NodeArg(n) for n in self._input_names]

    def get_outputs(self):
        return [_NodeArg(n) for n in self._output_names]

    def get_providers(self):
        return ["CPUExecutionProvider"]

    def io_binding(self) -> IOBinding:
        return IOBinding(self)

    def run_with_iobinding(self, iobinding: IOBinding = None, run_options=None, **kwargs) -> None:
        (iobinding or kwargs["iobinding"])._run()

    def run(self, output_names, input_feed: dict, run_options=None):
        names = list(input_feed)
        vals = [OrtValue.ortvalue_from_numpy(input_feed[n]) for n in names]
        outs = list(output_names) if output_names else self._output_names
        res = _bridge.run(self._h, names, [v._h for v in vals], outs)
        return [OrtValue(h).numpy() for h in res]


class SessionOptions:
    def __init__(self):
        self.intra_op_num_threads = 0
        self.inter_op_num_threads = 0
        self.graph_optimization_level = None
        self.log_severity_level = 3


class GraphOptimizationLevel:
    ORT_DISABLE_ALL = 0
    ORT_ENABLE_BASIC = 1
    ORT_ENABLE_EXTENDED = 2
    ORT_ENABLE_ALL = 99


def get_available_providers():
    return ["CPUExecutionProvider"]


def get_device():
    return "CPU"


def set_default_logger_severity(level: int) -> None:
    pass


def preload_dlls(*a, **k) -> None:
    pass


__version__ = "1.22.0-bridge"


class MockBridge:
    """The bridge API implemented with the real onnxruntime package (tests only)."""

    def __init__(self, real_ort):
        self.ort = real_ort
        self.sessions: dict[int, object] = {}
        self.by_path: dict[str, int] = {}
        self.tensors: dict[int, np.ndarray] = {}
        self.next = 1
        self.calls = 0

    def _new(self, table, obj) -> int:
        h = self.next
        self.next += 1
        table[h] = obj
        return h

    def open(self, path):
        # Like the Kotlin bridge: one cached session per model file
        if path not in self.by_path:
            s = self.ort.InferenceSession(path, providers=["CPUExecutionProvider"])
            self.by_path[path] = self._new(self.sessions, s)
        return self.by_path[path]

    def inputNames(self, h):
        return [i.name for i in self.sessions[h].get_inputs()]

    def outputNames(self, h):
        return [o.name for o in self.sessions[h].get_outputs()]

    def tensor(self, data, shape, dtype):
        arr = np.frombuffer(bytes(data), dtype=_NP[dtype]).reshape(shape)
        return self._new(self.tensors, arr)

    def run(self, h, names, handles, out_names):
        self.calls += 1
        feeds = {n: self.tensors[t] for n, t in zip(names, handles)}
        outs = self.sessions[h].run(list(out_names), feeds)
        return [self._new(self.tensors, np.ascontiguousarray(o)) for o in outs]

    def read(self, h):
        return self.tensors[h].tobytes()

    def shape(self, h):
        return list(self.tensors[h].shape)

    def dtype(self, h):
        return _DTYPES[self.tensors[h].dtype]

    def free(self, h):
        self.tensors.pop(h, None)

    def liveTensors(self):
        return len(self.tensors)
