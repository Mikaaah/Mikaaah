# Release identity rules

Use registry.json and actual JAR bytes when matching TNK versions. Never infer an outdated mod from public/internal number differences or replace a newer delivered source with an older default branch.

Each mod edition owns one deliberate release version. Do not bump it for every edit, rebuild or documentation change. New release versions use MAJOR.MINOR.PATCH with optional alpha.N, beta.N, rc.N or private.N. Keep Minecraft, loader and edition in separate fields. Generate JAR names, upload titles and receipts from release.json and check the actual packaged metadata with tnk_versions.py.

Historical aliases and fingerprints are immutable. A changed artifact cannot reuse a published release identity. Preserve standalone versus TNK edition requirements and per-port Java/dependency differences. Deprecated UltraTerraForged addons and retired resource packs remain frozen. Private previews do not become stable automatically.

Do not call identity validation a compilation, graphical client test, gameplay test, multiplayer test or full-pack certification. State where the Gradle adapter is installed and checked; the central registry alone does not enforce every repository's build pipeline.
