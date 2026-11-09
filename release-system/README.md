# TNK mod version matching

One version for a deliberate mod release. Its embedded metadata, JAR filename, changelog heading, Git tag and CurseForge upload title must agree. Minecraft, loader and edition are separate fields. Each mod keeps its own release sequence.

## Everyday rules

- Use `MAJOR.MINOR.PATCH`: a breaking compatibility change, a feature release, or a fix. Choose a release number deliberately; do not increment it for each edit, rebuild, description or documentation change.
- Test builds use `-alpha.N`, `-beta.N`, `-rc.N` or `-private.N`. A private preview is never automatically promoted to a public stable release.
- Ports implementing the same release share its version. Their Minecraft version, loader and Java requirements remain separate and must be tested independently.
- Use `<mod>-<version>[-<edition>]-<loader>-<minecraft>.jar`. Example: `flow-control-0.2.3-neoforge-1.21.1.jar`. This is a format example, not an instruction to publish a new Flow Control release.
- Use that exact JAR filename as the CurseForge upload title. Use `v<version>` as the Git tag, or `<edition>/v<version>` when a project has separate editions. One release tag can contain multiple platform artifacts.
- A rebuilt or changed JAR cannot replace a published version. Keep its checksum and release receipt. Unchanged existing releases remain unchanged.
- Never decide that a mod is outdated by comparing a display label with an internal version. Match its actual file hash first, then review confirmed code changes.

## Existing versions

`registry.json` records 14 projects, their supported target matrix, known published files and development references. `VERSION-MAP.md` is generated from that registry. The historical mappings are intentional aliases, not new release numbers:

| Public label | Actual embedded version | Treatment |
| --- | --- | --- |
| Flow Control 0.2.0 | 0.2.2 | Same bytes on all eight checked ports; current |
| Alchemical Lasers 1.0 | 1.0.0-rc.1 | Same bytes as the delivered candidate |
| Resonant Arcana 0.4.0 | 0.8.0 | Existing public release; 0.8.1 is a later hotfix candidate |
| Progression 1.0.0 | 1.2.0-private.1 | Existing public file; 1.5.0-private.1 is later development |
| Unidentified Loot 1.1 | 0.17.0-alpha | Standalone history; compare TNK development as a separate edition |

The UltraTerraForged addons are deprecated and frozen. The two resource packs are retired. Their history stays visible and release preparation rejects those projects. Duskdelvers has a source-verified version but no recorded JAR fingerprint. Wildshape and Combat Afloat have development artifacts; no public CurseForge artifact was established for them in this audit.

The default GitHub branch is not automatically authoritative. Workshop and Arcana currently have newer delivered source archives; Progression, Wildshape, Loot and Flow Control use their recorded development branches. Never substitute an older main branch for a newer delivered implementation.

## Check an existing download

Python 3.11 or newer is required; no third-party packages are needed.

```sh
python3 tnk_versions.py check
python3 tnk_versions.py list
python3 tnk_versions.py match /path/to/downloaded-mod.jar
python3 tnk_versions.py inspect /path/to/one.jar /path/to/another.jar
python3 -m unittest discover -s tests -v
```

`match` distinguishes an exact known file, a repack containing the same entries, and an unknown build. An unknown hash means review is needed; it does not mean the mod is outdated. Registry fingerprints cover the checked artifacts, not every historical release or new upload after the audit.

## Prepare the next release

1. Work in the correct mod repository, edition and development branch. Keep experimental Goliath work separate. Choose the version deliberately; the tool does not invent it.
2. Copy this directory into the mod checkout as `release-tools/`. Create `release.json` in the mod root with the `new` command below. Commit the release inputs before building.
3. Apply the Gradle adapter at the **end** of the root `build.gradle`: `apply from: 'release-tools/gradle/release.gradle'`. Remove superseded hard-coded version/filename overrides when adopting it. The adapter supplies the release version and stamps only the owned mod metadata.
4. Build and run the mod's normal checks. Run `tnkReleaseCheck` against the final distributable task. For Fabric use `-PtnkArtifactTask=remapJar`; Combat Punchy Forge uses `-PtnkArtifactTask=jarJar`, retaining reobfuscation and bundled dependencies. Wildshape uses its loader subproject, e.g. `-PtnkArtifactProject=:neoforge`, and the final remap/shadow task selected by its own build.
5. Run `record-build` for each verified candidate to update its development fingerprint. Run `prepare` with every target declared in `release.json`. It verifies the embedded identity, exact platform, Java bytecode, source commit, clean checkout and changelog, then copies the original bytes into a new folder with matching upload metadata and checksums.

```sh
# Run in the mod checkout; replace the example number with the intended release.
python3 release-tools/tnk_versions.py new flow-control 0.2.3 --out release.json
# Write CHANGELOG.md with a first version heading of 0.2.3 and real release notes.
# Commit the release inputs, then build each supported port with the same version.
./gradlew build -PtnkTarget=neoforge-1.21.1
python3 release-tools/tnk_versions.py verify --release release.json \
  --target neoforge-1.21.1 --jar build/libs/flow-control-0.2.3-neoforge-1.21.1.jar
python3 release-tools/tnk_versions.py record-build --release release.json \
  --target neoforge-1.21.1 --jar build/libs/flow-control-0.2.3-neoforge-1.21.1.jar
python3 release-tools/tnk_versions.py prepare --release release.json \
  --jar /path/to/each-supported-port.jar --out release-output
```

By default `new` includes the project's recorded target matrix. Use explicit `--target` options to declare only the ports actually supported by that release. A single-target manifest does not certify or silently update the other ports. Java 17, 21 and 25 requirements remain per target. Dependencies and gameplay/network compatibility remain the responsibility of the mod's own build and test workflow.

The generated folder contains the unchanged JARs, `release.json`, `SHA256SUMS` and `CHANGELOG.md`. Its receipt supplies the exact CurseForge titles and release types. A `private` receipt is for private distribution; do not publish it as stable. The commands do not upload files, change published pages, create tags or merge source branches.

## Record publication

After manually publishing a verified release, register its real file ID. The registry must already contain the verified CurseForge project ID and slug. This command downloads the public artifact and checks its SHA-256 before appending a published record. It never overwrites a historical record.

```sh
python3 release-tools/tnk_versions.py register --release release.json \
  --target neoforge-1.21.1 --jar release-output/flow-control-0.2.3-neoforge-1.21.1.jar \
  --file-id ACTUAL_FILE_ID --title flow-control-0.2.3-neoforge-1.21.1.jar
python3 release-tools/tnk_versions.py list --output release-tools/VERSION-MAP.md
```

Commit the updated registry to the central version directory and carry it into the next release's tooling. Pending approval, an unavailable Maven mirror or a mismatched upload prevents a public record being added. The upload title is checked against the supplied title; the public artifact bytes are checked independently. This is release identity verification, not a graphical client test or full-pack certification.

## Rollout status

The central registry and checker are ready. The Gradle adapter is opt-in and has an isolated fixture check. It is not silently installed into the individual mod repositories, and their existing build pipelines are not claimed to enforce it yet. Adopt it on each correct development branch after checking that branch's final JAR task; a generic adapter cannot replace loader-specific packaging tests.
