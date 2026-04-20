# Reverse Proxy Configuration

8mb.local streams real-time FFmpeg transcoding progress and logs using **Server-Sent Events (SSE)**. 

SSE requires specific proxy configuration to prevent buffering. Without disabling buffering, your proxy will hold the entire SSE stream and deliver all progress events at once when the job completes—meaning the progress bar will incorrectly appear stuck at 0% until the file is fully processed.

---

### Nginx / Nginx Proxy Manager

Add the following to your Nginx configuration block to disable proxy buffering for the API stream target:

```nginx
location /api/stream/ {
    proxy_pass http://backend:8001;
    proxy_buffering off;
    proxy_cache off;
    proxy_set_header Connection '';
    chunked_transfer_encoding on;
}
```

> **Note for Nginx Proxy Manager (NPM):** Edit your Proxy Host → click on the **Advanced** tab → paste the code above into the "Custom Nginx Configuration" text field.

---

### Traefik

Append these no-buffer middleware rules to your local docker-compose instance:

```yaml
labels:
  - "traefik.http.middlewares.no-buffer.buffering.maxRequestBodyBytes=0"
  - "traefik.http.middlewares.no-buffer.buffering.maxResponseBodyBytes=0"
  - "traefik.http.routers.8mb.local.middlewares=no-buffer"
```

---

### Apache

Add these directives to pass chunked responses correctly:

```apache
<Location /api/stream/>
    ProxyPass http://backend:8001/api/stream/
    ProxyPassReverse http://backend:8001/api/stream/
    SetEnv proxy-sendchunked 1
    SetEnv proxy-interim-response RFC
</Location>
```
