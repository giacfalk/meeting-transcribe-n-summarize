# Code signing

The release builds are **not code-signed** yet. Signing needs a certificate that is tied to
a verified identity, and that can't be set up from the code alone. This page explains what
unsigned means for users and what it takes to sign the builds.

## What unsigned means for users

- **Windows:** SmartScreen may show *"Windows protected your PC"* the first few times a
  new release is downloaded. Choose **More info → Run anyway**. SmartScreen builds up
  "reputation" per file, so the warning fades as more people download a release. Signing
  makes it go away much sooner.
- **macOS:** the app is ad-hoc signed but not notarized, so Gatekeeper asks you to confirm
  the first launch (right-click → **Open**).
- **Linux:** nothing changes. Check the `.sha256` file if you like.

Every release is built from the tagged source by GitHub Actions. The build logs are public,
and each file ships with a SHA-256 checksum.

## Options for signing the Windows build

| Option | Cost | Who can use it | Notes |
|---|---|---|---|
| [SignPath Foundation](https://signpath.org/) | free for open source | open-source projects that meet [its conditions](https://signpath.org/terms) | Signs builds produced by CI from the public repository. Each release is approved by hand. Integrates with GitHub Actions through [`SignPath/github-action-submit-signing-request`](https://github.com/SignPath/github-action-submit-signing-request). |
| [Azure Artifact Signing](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options) (formerly Trusted Signing) | about $10 / month | organizations in the USA, Canada and Europe; *individual* developers in the USA and Canada only | No hardware token needed. Has a first-party GitHub Action ([`Azure/artifact-signing-action`](https://github.com/Azure/artifact-signing-action)). |
| A commercial OV/EV certificate | about $200–400 / year | anyone, after identity checks | Since 2023 the private key must live on a hardware token or a cloud HSM, which makes CI signing more work. |

For this project, **SignPath Foundation** is the natural fit: it's free and made for
open-source projects. Its conditions include:

- An OSI-approved license (GPL-3.0 qualifies).
- **No proprietary components in the signed build.** Check this one: the CTranslate2 wheel
  bundles NVIDIA's `cudnn64_9.dll` (for GPU transcription) and Intel's `libiomp5md.dll`.
  Leaving cuDNN out of the bundle would mean GPU users install cuDNN themselves, which
  they need CUDA for anyway.
- Multi-factor authentication for everyone with commit or approval rights.
- A *Code signing policy* section in the README with the required attribution.

### Wiring it into CI

Once a signing service is set up, signing is one extra step in the `build` job of
[`.github/workflows/ci.yml`](../.github/workflows/ci.yml), between *Build* and *Smoke-test
the build*:

1. Sign `dist/MeetingRecorder/MeetingRecorder.exe` with the service's GitHub Action.
2. Re-create the zip and the installer from the signed files, then sign the installer
   (`…-setup.exe`) as well.

The credentials go into the repository's *Actions secrets*, never into the code.

## macOS

Removing the Gatekeeper prompt requires an Apple Developer ID certificate ($99 / year) and
notarization (`xcrun notarytool`), done in the macOS build job.
