plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("com.google.devtools.ksp")
}

val releaseSigningValues = mapOf(
    "storeFile" to providers.environmentVariable("ANDROID_SIGNING_STORE_FILE").orNull,
    "storePassword" to providers.environmentVariable("ANDROID_SIGNING_STORE_PASSWORD").orNull,
    "keyAlias" to providers.environmentVariable("ANDROID_SIGNING_KEY_ALIAS").orNull,
    "keyPassword" to providers.environmentVariable("ANDROID_SIGNING_KEY_PASSWORD").orNull,
)
val releaseSigningConfigured = releaseSigningValues.values.all { !it.isNullOrBlank() }
if (!releaseSigningConfigured && releaseSigningValues.values.any { !it.isNullOrBlank() }) {
    throw GradleException("Android release signing is only partially configured; provide all four ANDROID_SIGNING_* variables.")
}

android {
    namespace = "com.jms1717.eightmblocal"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.jms1717.eightmblocal"
        minSdk = 26
        targetSdk = 36
        versionCode = 143000000
        versionName = "143.0.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    signingConfigs {
        if (releaseSigningConfigured) {
            create("release") {
                storeFile = file(releaseSigningValues.getValue("storeFile")!!)
                storePassword = releaseSigningValues.getValue("storePassword")
                keyAlias = releaseSigningValues.getValue("keyAlias")
                keyPassword = releaseSigningValues.getValue("keyPassword")
            }
        }
    }
    buildTypes {
        getByName("release") {
            if (releaseSigningConfigured) signingConfig = signingConfigs.getByName("release")
        }
    }

    buildFeatures { compose = true }
    packaging { resources.excludes += "/META-INF/{AL2.0,LGPL2.1}" }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

tasks.register("verifyReleaseSigning") {
    group = "verification"
    description = "Fails unless the permanent Android release signing configuration is complete."
    doLast {
        check(releaseSigningConfigured) {
            "Release signing is required. Configure ANDROID_SIGNING_STORE_FILE, " +
                "ANDROID_SIGNING_STORE_PASSWORD, ANDROID_SIGNING_KEY_ALIAS, and ANDROID_SIGNING_KEY_PASSWORD."
        }
        check(file(releaseSigningValues.getValue("storeFile")!!).isFile) {
            "ANDROID_SIGNING_STORE_FILE does not point to a readable keystore."
        }
    }
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2025.08.01"))
    implementation("androidx.activity:activity-compose:1.13.0")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    debugImplementation("androidx.compose.ui:ui-tooling")
    implementation("androidx.core:core-ktx:1.17.0")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.10.0")
    implementation("androidx.media3:media3-common:1.11.0")
    implementation("androidx.media3:media3-effect:1.11.0")
    implementation("androidx.media3:media3-exoplayer:1.11.0")
    implementation("androidx.media3:media3-transformer:1.11.0")
    implementation("androidx.media3:media3-ui:1.11.0")
    implementation("androidx.room:room-runtime:2.8.4")
    implementation("androidx.room:room-ktx:2.8.4")
    ksp("androidx.room:room-compiler:2.8.4")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")
    implementation("com.google.guava:guava:33.5.0-android")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.10.2")
    androidTestImplementation(platform("androidx.compose:compose-bom:2025.08.01"))
    androidTestImplementation("androidx.test.ext:junit:1.3.0")
    androidTestImplementation("androidx.test:rules:1.7.0")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.7.0")
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
    debugImplementation("androidx.compose.ui:ui-test-manifest")
}
