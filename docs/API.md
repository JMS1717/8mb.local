# 8mb.local API

The Docker and Windows applications expose the same FastAPI service. OpenAPI
documentation is available at `/docs` and `/openapi.json` while the app is
running. The examples below assume the default local URL and disabled auth.

## Single-file workflow

```powershell
$upload = curl.exe -s -F "file=@input.mp4" -F "target_size_mb=19.7" http://127.0.0.1:8001/api/upload | ConvertFrom-Json
$job = curl.exe -s -H "Content-Type: application/json" -d (@{
  job_id=$upload.job_id; filename=$upload.filename; target_size_mb=19.7
  video_codec='libx264'; audio_codec='aac'; audio_bitrate_kbps=128
  preset='p6'; container='mp4'; tune='hq'
} | ConvertTo-Json -Compress) http://127.0.0.1:8001/api/compress | ConvertFrom-Json

do {
  Start-Sleep -Seconds 1
  $status = curl.exe -s http://127.0.0.1:8001/api/jobs/$($job.task_id)/status | ConvertFrom-Json
} while ($status.state -notin @('SUCCESS','COMPLETED','FAILURE','FAILED'))

curl.exe -L http://127.0.0.1:8001/api/jobs/$($job.task_id)/download -o output.mp4
```

The output is valid only after the job reaches `SUCCESS` or `COMPLETED`. Use
`POST /api/jobs/{task_id}/cancel` to request cancellation.

## Batch workflow

```powershell
curl.exe -s -F "files=@one.mp4" -F "files=@two.mp4" `
  -F "video_codec=libx264" -F "audio_codec=aac" `
  http://127.0.0.1:8001/api/batches/upload
curl.exe -s http://127.0.0.1:8001/api/batches/{batch_id}/status
curl.exe -L http://127.0.0.1:8001/api/batches/{batch_id}/download.zip -o batch.zip
```

Batch items are independent. A failed item is reported in the batch status;
other valid items continue. Multipart parser temporary files and API-staged
inputs use the configured upload storage path, and transient inputs are
removed after processing or failure.

## Authentication

When `AUTH_ENABLED=true`, send HTTP Basic authentication on every protected
request. Do not put credentials in URLs or scripts checked into the project.

```powershell
$auth = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes('user:password'))
curl.exe -H "Authorization: Basic $auth" http://127.0.0.1:8001/api/jobs/{task_id}/status
```

Protected downloads also require the same credentials. Browser and WebView
clients should use the application UI so the origin's authentication session
can be reused; API clients should send the header explicitly.
