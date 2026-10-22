plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

// Release signing comes from the environment (set by CI from repository secrets).
val keystorePath: String? = System.getenv("PR_KEYSTORE")
val hasReleaseKey = keystorePath != null && file(keystorePath).exists()

android {
    namespace = "com.ilias.practiceroom"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.ilias.practiceroom"
        minSdk = 29  // Android 10: MediaStore Downloads, scoped storage
        targetSdk = 35
        versionCode = (System.getenv("PR_VERSION_CODE") ?: "1").toInt()
        versionName = System.getenv("PR_VERSION_NAME") ?: "0.1.0-dev"
    }

    // One APK per CPU type (Chaquopy needs ndk.abiFilters, which can't be combined with ABI splits)
    flavorDimensions += "abi"
    productFlavors {
        create("phone") {
            dimension = "abi"
            ndk { abiFilters += "arm64-v8a" }
        }
        create("emu") {   // the CI emulator test
            dimension = "abi"
            ndk { abiFilters += "x86_64" }
        }
    }

    signingConfigs {
        if (hasReleaseKey) {
            create("release") {
                storeFile = file(keystorePath!!)
                storePassword = System.getenv("PR_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("PR_KEY_ALIAS")
                keyPassword = System.getenv("PR_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = if (hasReleaseKey) signingConfigs.getByName("release")
                            else signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }

    packaging {
        jniLibs {
            useLegacyPackaging = false
        }
    }
}

chaquopy {
    defaultConfig {
        // Chaquopy's OpenCV build exists for Python 3.10 (tested: homr runs unchanged on it)
        version = "3.10"
        System.getenv("PR_BUILD_PYTHON")?.let { buildPython(it) }
        // homr, tokenizers and music21 read data files next to their modules
        extractPackages("homr", "music21")
        pip {
            // Dependencies are listed explicitly so nothing heavy (matplotlib) or
            // unavailable (onnxruntime: replaced by the native bridge) gets pulled in.
            options("--no-deps")
            install("numpy")
            install("opencv-python-headless")
            install("pillow")
            install("music21==9.9.2")
            install("musicxml==1.4")
            install("typing_extensions==4.16.0")
            install("jsonpickle==4.1.4")
            install("more-itertools==11.1.0")
            install("webcolors==25.10.0")
            install("joblib==1.6.0")
            install("chardet==5.2.0")
            install("requests==2.34.2")
            install("urllib3==2.8.0")
            install("idna==3.20")
            install("certifi==2026.10.19")
            install("charset-normalizer==3.5.2")
        }
    }
}

dependencies {
    implementation("com.microsoft.onnxruntime:onnxruntime-android:1.22.0")
    implementation("androidx.activity:activity-ktx:1.9.3")
    implementation("androidx.core:core-ktx:1.15.0")
}
