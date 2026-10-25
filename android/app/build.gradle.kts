plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Release builds are signed with the key from these environment variables (see android/README.md).
// Without them, release builds use the debug key, which is fine for trying the app yourself.
val keystore: String? = System.getenv("MANGAVIBE_KEYSTORE")

android {
    namespace = "io.github.ivokolarov0.mangavibe"
    compileSdk = 35

    defaultConfig {
        applicationId = "io.github.ivokolarov0.mangavibe"
        minSdk = 26
        targetSdk = 35
        // CI passes these from the release tag: -PversionName=1.4.0 -PversionCode=12
        versionCode = (findProperty("versionCode") as String?)?.toInt() ?: 1
        versionName = (findProperty("versionName") as String?) ?: "dev"
    }

    signingConfigs {
        if (keystore != null) {
            create("release") {
                storeFile = file(keystore)
                storePassword = System.getenv("MANGAVIBE_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("MANGAVIBE_KEY_ALIAS")
                keyPassword = System.getenv("MANGAVIBE_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}
