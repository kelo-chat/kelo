#!/usr/bin/env python3
"""Writes desktop/src/main/kotlin/chat/kelo/app/my/BriefingModels.kt from the plain parts (models and rules) of the Android briefing
settings files, which also hold Android Compose and so cannot be shared as they are.

    python3 desktop/tools/copy_briefing_models.py
"""
from pathlib import Path
root = str(Path(__file__).resolve().parents[2])
my = root + '/android/kelo/app/src/main/java/chat/kelo/app/my/'
a = open(my + 'BriefingSettings.kt').read()
b = open(my + 'BriefingSources.kt').read()
p1 = a[a.index('/** Where a phone of the owner'):a.index('@Composable\nprivate fun providerLabel')].rstrip()
p2 = b[b.index('/** One account'):b.index('/**\n * "What it reads"')].rstrip()
out = '''package chat.kelo.app.my

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

// The plain parts of android/kelo .../my/BriefingSettings.kt and my/BriefingSources.kt (those files also draw the settings with
// Android Compose), copied by hand so the desktop's settings page and the Android unit tests use the same model and rules.
// Keep in step with them (desktop/tools/copy_briefing_models.py writes this file).

''' + p1 + '\n\n' + p2 + '\n'
open(root + '/desktop/src/main/kotlin/chat/kelo/app/my/BriefingModels.kt', 'w').write(out)
