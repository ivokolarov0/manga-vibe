# The web page calls these methods by name (window.MangaVibeAndroid.*)
-keepclassmembers class io.github.ivokolarov0.mangavibe.Bridge {
    @android.webkit.JavascriptInterface <methods>;
}
