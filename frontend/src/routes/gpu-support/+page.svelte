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
</style>

<div class="container">
  <div class="hdr">
	<h1 class="title" style="font-size:32px; text-align:left; margin:0">GPU Encoding Support</h1>
	<a href="/settings" class="btn">&larr; Back to Settings</a>
  </div>

  <p class="subtitle">NVIDIA NVENC hardware encoders supported by 8mb.local</p>

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
		<thead>
		  <tr>
			<th>GPU Series</th>
			<th>Example GPUs</th>
			<th>Encoder Hardware</th>
			<th>Codec Support (Encode)</th>
		  </tr>
		</thead>
		<tbody>
		  <tr>
			<td class="gpu-series">RTX 50 Series (Blackwell)</td>
			<td>RTX 5090, RTX 5080, RTX 5070 Ti, RTX 5070</td>
			<td>9th Gen NVENC (x2)</td>
			<td class="codec-support">
			  <span class="av1-text">AV1</span>
			  <span class="hevc-text">HEVC</span>
			  <span class="h264-text">H.264</span>
			</td>
		  </tr>
		  <tr>
			<td class="gpu-series">RTX 40 Series (Ada)</td>
			<td>RTX 4090, 4080, 4070 Ti, 4060, 4050 (Laptop)</td>
			<td>8th Gen NVENC</td>
			<td class="codec-support">
			  <span class="av1-text">AV1</span>
			  <span class="hevc-text">HEVC</span>
			  <span class="h264-text">H.264</span>
			</td>
		  </tr>
		  <tr>
			<td class="gpu-series">RTX 30 Series (Ampere)</td>
			<td>RTX 3090 Ti, 3080, 3070, 3060, 3050</td>
			<td>7th Gen NVENC</td>
			<td class="codec-support">
			  <span class="no-av1">(No AV1 Encode)</span><br>
			  <span class="hevc-text">HEVC</span>
			  <span class="h264-text">H.264</span>
			</td>
		  </tr>
		  <tr>
			<td class="gpu-series">RTX 20 Series (Turing)</td>
			<td>RTX 2080 Ti, 2070 Super, 2060</td>
			<td>6th Gen NVENC</td>
			<td class="codec-support">
			  <span class="hevc-text">HEVC</span>
			  <span class="h264-text">H.264</span>
			</td>
		  </tr>
		  <tr>
			<td class="gpu-series">GTX 16 Series (Turing)</td>
			<td>GTX 1660 Super, 1660 Ti, 1650 Super</td>
			<td>6th Gen NVENC</td>
			<td class="codec-support">
			  <span class="hevc-text">HEVC</span>
			  <span class="h264-text">H.264</span>
			</td>
		  </tr>
		  <tr>
			<td class="gpu-series">GTX 10 Series (Pascal)</td>
			<td>GTX 1080 Ti, 1070, 1060, 1050</td>
			<td>5th Gen NVENC</td>
			<td class="codec-support">
			  <span class="hevc-text">HEVC (8-bit)</span>
			  <span class="h264-text">H.264</span>
			</td>
		  </tr>
		</tbody>
	  </table>
	</div>
  </section>

  <!-- Footer note -->
  <div style="background:#1f2937; border:1px solid #374151; border-radius:12px; padding:20px; margin-top:48px">
	<p style="color:#9ca3af; font-size:14px; margin:0">
	  <strong style="color:#d1d5db">Note:</strong> 8mb.local supports NVIDIA GPUs with NVENC hardware encoding. Systems without a supported NVIDIA GPU will use CPU software encoding (libx264, libx265, libaom-av1) which is slower but universally compatible. The codec dropdown on the main page will only show encoders that are actually available on your system.
	</p>
  </div>
</div>
