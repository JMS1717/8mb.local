# Google Play submission package

This directory contains the copy, graphics, policy answers, release notes, and
foreground-service evidence needed to prepare the `com.jms1717.eightmblocal`
listing. Nothing here publishes or changes Play Console state.

Run `generate-assets.ps1` after replacing or adding deterministic screenshots
under `source-screenshots`. Phone screenshots are normalized to 1080 by 2160;
the store icon is 512 by 512 and the feature graphic is 1024 by 500.

Before submission:

1. Merge the privacy policy so its stable `main` URL is live.
2. Complete the answers in `app-content.md` in Play Console.
3. Upload the listing copy and graphics under `listing/en-US`.
4. Upload the signed AAB produced by the signed-release workflow.
5. Upload `listing/en-US/graphics/foreground-service-demo.mp4` as an unlisted,
   embeddable video and use its URL in both foreground-service declarations.

If the permanent Android upload identity does not exist yet, run
`../configure-release-signing.ps1`. It prompts locally, writes the keystore
outside the repository under Documents by default, and sends the four values
directly to GitHub Actions secrets without writing passwords to this repository.
