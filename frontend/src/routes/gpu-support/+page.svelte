<script lang="ts">
</script>

<style>
  .container { max-width: 1280px; margin: 0 auto; padding: 24px; }
  .title { color: white; font-size: 28px; font-weight: 700; text-align: center; margin-bottom: 16px; }
  .subtitle { color: #9ca3af; text-align: center; margin-bottom: 48px; }
  .key { display: flex; flex-wrap: wrap; justify-content: center; gap: 16px; margin-bottom: 48px; }
  .badge { font-size: 14px; font-weight: 600; padding: 8px 16px; border-radius: 9999px; }
  .badge.av1 { background: #15803d; color: #d1fae5; }
  .badge.hevc { background: #1d4ed8; color: #dbeafe; }
  .badge.h264 { background: #4b5563; color: #e5e7eb; }
  .section { margin-bottom: 64px; }
  .section-title { color: white; font-size: 24px; font-weight: 600; margin-bottom: 24px; padding-bottom: 8px; border-bottom: 2px solid; }
  .section-title.nvidia { color: #10b981; border-color: #10b981; }
  .section-title.qsv { color: #60a5fa; border-color: #60a5fa; }
  .section-title.vaapi { color: #f59e0b; border-color: #f59e0b; }
  .table-wrapper { overflow-x: auto; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); background: #1f2937; }
  table { width: 100%; min-width: 100%; }
  thead { background: #374151; }
  th { padding: 16px; text-align: left; font-size: 12px; font-weight: 600; text-transform: uppercase; color: #d1d5db; }
  tbody { background: #1f2937; }
  tr { border-bottom: 1px solid #374151; }
  td { padding: 16px; vertical-align: top; color: #e5e7eb; }
  td.gpu-series { font-weight: 600; color: white; }
  .codec-support span { display: inline-block; margin-right: 8px; }
  .av1-text { font-weight: 700; color: #10b981; }
  .hevc-text { font-weight: 700; color: #60a5fa; }
  .h264-text { color: #d1d5db; }
  .no-av1 { color: #facc15; }
  .hdr { display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px; }
  .btn { padding: 10px 16px; color: white; background: #374151; border: none; border-radius: 8px; cursor: pointer; text-decoration: none; display: inline-block; }
  .btn:hover { background: #4b5563; }
  .info-box { background:#1f2937; border:1px solid #374151; border-radius:12px; padding:20px; margin-top:24px; }
  .info-box p { color:#9ca3af; font-size:14px; margin:0; }
  .info-box code { background:#374151; padding:2px 6px; border-radius:4px; font-size:13px; color:#d1d5db; }
</style>

<div class="container">
  <div class="hdr">
        <h1 class="title" style="font-size:32px; text-align:left; margin:0">Hardware Encoding Support</h1>
        <a href="/settings" class="btn">&larr; Back to Settings</a>
  </div>

  <p class="subtitle">NVIDIA NVENC, Intel QSV, and VAAPI hardware encoders supported by 8mb.local</p>

  <!-- Key -->
  <div class="key">
        <span class="badge av1">AV1 Encode (Best)</span>
        <span class="badge hevc">HEVC (H.265)</span>
        <span class="badge h264">H.264 (AVC)</span>
  </div>

  <!-- NVIDIA Section -->
  <section class="section">
        <h2 class="section-title nvidia">NVIDIA (NVENC)</h2>
        <div class="table-wrapper">
          <table>
                <thead><tr><th>GPU Series</th><th>Example GPUs</th><th>Encoder Hardware</th><th>Codec Support (Encode)</th></tr></thead>
                <tbody>
                  <tr><td class="gpu-series">RTX 50 Series (Blackwell)</td><td>RTX 5090, 5080, 5070 Ti, 5070</td><td>9th Gen NVENC (x2)</td><td class="codec-support"><span class="av1-text">AV1</span><span class="hevc-text">HEVC</span><span class="h264-text">H.264</span></td></tr>
                  <tr><td class="gpu-series">RTX 40 Series (Ada)</td><td>RTX 4090, 4080, 4070 Ti, 4060, 4050</td><td>8th Gen NVENC</td><td class="codec-support"><span class="av1-text">AV1</span><span class="hevc-text">HEVC</span><span class="h264-text">H.264</span></td></tr>
                  <tr><td class="gpu-series">RTX 30 Series (Ampere)</td><td>RTX 3090 Ti, 3080, 3070, 3060, 3050</td><td>7th Gen NVENC</td><td class="codec-support"><span class="no-av1">(No AV1)</span><br><span class="hevc-text">HEVC</span><span class="h264-text">H.264</span></td></tr>
                  <tr><td class="gpu-series">RTX 20 Series (Turing)</td><td>RTX 2080 Ti, 2070 Super, 2060</td><td>6th Gen NVENC</td><td class="codec-support"><span class="hevc-text">HEVC</span><span class="h264-text">H.264</span></td></tr>
                  <tr><td class="gpu-series">GTX 16 Series (Turing)</td><td>GTX 1660 Super, 1660 Ti, 1650 Super</td><td>6th Gen NVENC</td><td class="codec-support"><span class="hevc-text">HEVC</span><span class="h264-text">H.264</span></td></tr>
                  <tr><td class="gpu-series">GTX 10 Series (Pascal)</td><td>GTX 1080 Ti, 1070, 1060, 1050</td><td>5th Gen NVENC</td><td class="codec-support"><span class="hevc-text">HEVC (8-bit)</span><span class="h264-text">H.264</span></td></tr>
                </tbody>
          </table>
        </div>
  </section>

  <!-- Intel QSV Section -->
  <section class="section">
        <h2 class="section-title qsv">Intel Quick Sync Video (QSV)</h2>
        <p style="color:#9ca3af; margin-bottom:16px">QSV is initialized via the VAAPI backend (<code>-init_hw_device vaapi=va:DEV -init_hw_device qsv=qs@va</code>) for maximum compatibility. No libmfx required.</p>
        <div class="table-wrapper">
          <table>
                <thead><tr><th>Platform</th><th>Example CPUs / GPUs</th><th>Media Engine</th><th>Codec Support (Encode)</th></tr></thead>
                <tbody>
                  <tr><td class="gpu-series">Arrow Lake / Lunar Lake (2024+)</td><td>Core Ultra 200, Core Ultra 200V</td><td>Xe2 Media Engine</td><td class="codec-support"><span class="av1-text">AV1</span><span class="hevc-text">HEVC</span><span class="h264-text">H.264</span></td></tr>
                  <tr><td class="gpu-series">Meteor Lake (14th Gen, 2024)</td><td>Core Ultra 5/7/9 (MTL)</td><td>Xe LPG Media</td><td class="codec-support"><span class="av1-text">AV1</span><span class="hevc-text">HEVC</span><span class="h264-text">H.264</span></td></tr>
                  <tr><td class="gpu-series">Arc GPUs (Alchemist, 2022)</td><td>Arc A770, A750, A380</td><td>Xe HPG Media</td><td class="codec-support"><span class="av1-text">AV1</span><span class="hevc-text">HEVC</span><span class="h264-text">H.264</span></td></tr>
                  <tr><td class="gpu-series">Alder Lake (12th Gen) / Raptor Lake (13th Gen)</td><td>i5-12400, i7-12700K, i5-13600K, N100</td><td>Xe LP Media</td><td class="codec-support"><span class="no-av1">(No AV1)</span><br><span class="hevc-text">HEVC</span><span class="h264-text">H.264</span></td></tr>
                  <tr><td class="gpu-series">Tiger Lake (11th Gen)</td><td>i5-1135G7, i7-1165G7</td><td>Xe LP Media</td><td class="codec-support"><span class="no-av1">(No AV1)</span><br><span class="hevc-text">HEVC</span><span class="h264-text">H.264</span></td></tr>
                  <tr><td class="gpu-series">Comet/Ice Lake (10th Gen)</td><td>i5-10400, i7-1065G7</td><td>Gen11/Gen12 EU</td><td class="codec-support"><span class="hevc-text">HEVC</span><span class="h264-text">H.264</span></td></tr>
                  <tr><td class="gpu-series">Coffee/Kaby Lake (8th-9th Gen)</td><td>i5-8400, i7-9700K</td><td>Gen9.5 EU</td><td class="codec-support"><span class="hevc-text">HEVC (8-bit)</span><span class="h264-text">H.264</span></td></tr>
                  <tr><td class="gpu-series">Skylake (6th Gen)</td><td>i5-6500, i7-6700K</td><td>Gen9 EU</td><td class="codec-support"><span class="hevc-text">HEVC (8-bit)</span><span class="h264-text">H.264</span></td></tr>
                  <tr><td class="gpu-series">Jasper Lake / Elkhart Lake (NAS)</td><td>J4125, J5005, N5105, N6005</td><td>Gen11 EU</td><td class="codec-support"><span class="hevc-text">HEVC</span><span class="h264-text">H.264</span></td></tr>
                </tbody>
          </table>
        </div>
        <div class="info-box">
          <p><strong style="color:#d1d5db">Synology NAS:</strong> Mount host GPU libraries via volumes: <code>/lib/modules:/lib/modules:ro</code> and <code>/usr/lib:/host/usr/lib:ro</code>. Set <code>LIBVA_DRIVERS_PATH=/host/usr/lib/x86_64-linux-gnu/dri</code>.</p>
        </div>
  </section>

  <!-- VAAPI Section -->
  <section class="section">
        <h2 class="section-title vaapi">VAAPI (Intel / AMD)</h2>
        <p style="color:#9ca3af; margin-bottom:16px">VAAPI is a universal Linux hardware acceleration API. It works as a fallback when QSV is unavailable and supports both Intel and AMD GPUs.</p>
        <div class="table-wrapper">
          <table>
                <thead><tr><th>Vendor / Platform</th><th>Driver</th><th>Codec Support (Encode)</th><th>Notes</th></tr></thead>
                <tbody>
                  <tr><td class="gpu-series">Intel 8th Gen+ (iHD driver)</td><td><code>intel-media-va-driver-non-free</code> (iHD)</td><td class="codec-support"><span class="hevc-text">HEVC</span><span class="h264-text">H.264</span> (AV1 on 12th Gen+)</td><td>Recommended for modern Intel. Set <code>LIBVA_DRIVER_NAME=iHD</code></td></tr>
                  <tr><td class="gpu-series">Intel pre-8th Gen (i965 driver)</td><td><code>i965-va-driver</code></td><td class="codec-support"><span class="h264-text">H.264</span> (HEVC varies)</td><td>Legacy driver. Set <code>LIBVA_DRIVER_NAME=i965</code></td></tr>
                  <tr><td class="gpu-series">AMD RDNA / GCN (Mesa)</td><td><code>mesa-va-drivers</code></td><td class="codec-support"><span class="hevc-text">HEVC</span><span class="h264-text">H.264</span></td><td>Requires Mesa 22+ for good VAAPI encode support</td></tr>
                </tbody>
          </table>
        </div>
        <div class="info-box">
          <p><strong style="color:#d1d5db">Auto-detection:</strong> 8mb.local automatically detects the best VAAPI driver (iHD → i965 → system default) at startup. Override with <code>LIBVA_DRIVER_NAME</code> environment variable if needed. The VAAPI device defaults to <code>/dev/dri/renderD128</code> — override with <code>VAAPI_DEVICE</code>.</p>
        </div>
  </section>

  <!-- Footer note -->
  <div class="info-box" style="margin-top:48px">
        <p>
          <strong style="color:#d1d5db">Note:</strong> 8mb.local supports NVIDIA NVENC, Intel QSV (via VAAPI backend), and VAAPI hardware encoding. The encoder priority is: NVENC → QSV → VAAPI → CPU. Systems without a supported GPU will use CPU software encoding (libx264, libx265, libaom-av1). Use <code>NO_QSV=true</code> or <code>NO_VAAPI=true</code> environment variables to disable specific hardware paths.
        </p>
  </div>
</div>
