# kotlinx.serialization, OkHttp, CameraX and ML Kit ship their own consumer rules.
# Keep the @Serializable models' generated serializers (belt and braces for R8 full mode).
-keepclassmembers @kotlinx.serialization.Serializable class chat.kelo.app.** {
    *** Companion;
    kotlinx.serialization.KSerializer serializer(...);
}
-keep,includedescriptorclasses class chat.kelo.app.**$$serializer { *; }

# Firebase Cloud Messaging ships its own consumer rules; the service the manifest names is kept here as well, and the
# serializable models of the agent screen and notifications are covered by the rules above (chat.kelo.app.**).
-keep class chat.kelo.app.notify.KeloMessagingService { *; }
-keep class chat.kelo.app.notify.PollJobService { <init>(); }
