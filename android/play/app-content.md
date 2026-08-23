# Play Console app-content answers

These answers describe the v143 Android build and must be rechecked if
dependencies, permissions, or network behavior change.

## Privacy and data safety

- Privacy policy URL after merge: `https://github.com/JMS1717/8mb.local/blob/main/PRIVACY.md`
- Collects or shares required user-data types: **No**. Media is accessed and
  processed only on-device. Sharing is an explicit user-initiated action.
- Ads: **No**.
- Accounts: **No accounts or login required**.
- Restricted access for reviewers: **No**.
- News app: **No**.
- Health app: **No**.
- Financial features: **No**.
- Target audience recommendation: **13 and older; not designed for children**.

## Foreground-service declarations

### Media processing

- Permission/type: `FOREGROUND_SERVICE_MEDIA_PROCESSING` / `mediaProcessing`
- Functionality: A user selects media and taps Compress and save or Extract and
  save audio. The foreground service transcodes the selected media and displays
  continuous progress in a notification until the export completes or is
  cancelled.
- Deferred impact: The explicitly requested export would not begin immediately.
- Interrupted impact: The expected output can be incomplete and must be restarted.
- User control: The app and notification expose progress; the app provides a
  Cancel action and stops the service when work completes.

### Data sync compatibility path

- Permission/type: `FOREGROUND_SERVICE_DATA_SYNC` / `dataSync`
- Functionality: On Android 8 through Android 14, the same user-initiated local
  media export uses the platform's data-sync foreground type because the
  dedicated media-processing type was introduced in Android 15.
- Deferred/interrupted impact and user control: Same as media processing above.

### Demonstration video

Upload `listing/en-US/graphics/foreground-service-demo.mp4` as an unlisted,
embeddable video with ads disabled, then paste that URL into both declarations.
The recording uses only an automatically generated test video.

## Content rating guidance

The app is a media utility. It contains no built-in violence, sexual content,
gambling, controlled substances, user communication, location sharing, or
unrestricted web access. User-selected media is not supplied by the app.

## Store contact and review notes

- Support: `https://github.com/JMS1717/8mb.local/issues`
- Review access: No credentials or special setup required.
- Review note: Select a local video through the system Photo Picker, accept the
  notification prompt on Android 13+, and tap Compress and save. Output appears
  in `DCIM/8mb.local`; no network connection is used.
