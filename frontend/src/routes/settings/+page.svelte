<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';

	interface AuthSettings {
		auth_enabled: boolean;
		auth_user: string | null;
	}

	interface DefaultPresets {
		target_mb: number;
		video_codec: string;
		audio_codec: string;
		preset: string;
		audio_kbps: number;
		container: string;
		tune: string;
	}

	let loading = $state(true);
	let saving = $state(false);
	let message = $state('');
	let error = $state('');

	// Auth settings
	let authEnabled = $state(false);
	let username = $state('admin');
	let newPassword = $state('');
	let confirmPassword = $state('');

	// Password change
	let showPasswordChange = $state(false);
	let currentPassword = $state('');
	let changePassword = $state('');
	let changeConfirmPassword = $state('');

	// Support section
	let showSupport = $state(false);

	// Run instructions section
	let showRunHelp = $state(false);

	// Default presets
	let targetMB = $state(25);
	let videoCodec = $state('av1_nvenc');
	let audioCodec = $state('libopus');
	let preset = $state('p6');
	let audioKbps = $state(128);
	let container = $state('mp4');
	let tune = $state('hq');

	async function loadSettings() {
		try {
			const [authRes, presetsRes] = await Promise.all([
				fetch('/api/settings/auth'),
				fetch('/api/settings/presets')
			]);

			if (authRes.ok) {
				const data: AuthSettings = await authRes.json();
				authEnabled = data.auth_enabled;
				username = data.auth_user || 'admin';
			}

			if (presetsRes.ok) {
				const data: DefaultPresets = await presetsRes.json();
				targetMB = data.target_mb;
				videoCodec = data.video_codec;
				audioCodec = data.audio_codec;
				preset = data.preset;
				audioKbps = data.audio_kbps;
				container = data.container;
				tune = data.tune;
			}
		} catch (err) {
			error = 'Failed to load settings';
		} finally {
			loading = false;
		}
	}

	async function saveSettings() {
		error = '';
		message = '';

		// Validation
		if (authEnabled && !username.trim()) {
			error = 'Username is required when authentication is enabled';
			return;
		}

		if (authEnabled && newPassword && newPassword !== confirmPassword) {
			error = 'Passwords do not match';
			return;
		}

		if (authEnabled && newPassword && newPassword.length < 4) {
			error = 'Password must be at least 4 characters';
			return;
		}

		saving = true;

		try {
			const payload: any = {
				auth_enabled: authEnabled,
				auth_user: username.trim()
			};

			// Include password only if it's being set
			if (authEnabled && newPassword) {
				payload.auth_pass = newPassword;
			}

			const res = await fetch('/api/settings/auth', {
				method: 'PUT',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(payload)
			});

			if (res.ok) {
				const data = await res.json();
				message = data.message || 'Settings saved successfully!';
				newPassword = '';
				confirmPassword = '';

				// If auth was disabled, reload to reflect changes
				if (!authEnabled) {
					setTimeout(() => {
						window.location.href = '/';
					}, 1500);
				}
			} else {
				const data = await res.json();
				error = data.detail || 'Failed to save settings';
			}
		} catch (err) {
			error = 'Failed to save settings';
		} finally {
			saving = false;
		}
	}

	async function savePresets() {
		error = '';
		message = '';

		// Validation
		if (targetMB < 1) {
			error = 'Target size must be at least 1 MB';
			return;
		}

		saving = true;

		try {
			const res = await fetch('/api/settings/presets', {
				method: 'PUT',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					target_mb: targetMB,
					video_codec: videoCodec,
					audio_codec: audioCodec,
					preset: preset,
					audio_kbps: audioKbps,
					container: container,
					tune: tune
				})
			});

			if (res.ok) {
				const data = await res.json();
				message = data.message || 'Default presets saved successfully!';
			} else {
				const data = await res.json();
				error = data.detail || 'Failed to save presets';
			}
		} catch (err) {
			error = 'Failed to save presets';
		} finally {
			saving = false;
		}
	}

	async function handlePasswordChange() {
		error = '';
		message = '';

		if (!currentPassword || !changePassword) {
			error = 'All password fields are required';
			return;
		}

		if (changePassword !== changeConfirmPassword) {
			error = 'New passwords do not match';
			return;
		}

		if (changePassword.length < 4) {
			error = 'Password must be at least 4 characters';
			return;
		}

		saving = true;

		try {
			const res = await fetch('/api/settings/password', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					current_password: currentPassword,
					new_password: changePassword
				})
			});

			if (res.ok) {
				message = 'Password changed successfully!';
				currentPassword = '';
				changePassword = '';
				changeConfirmPassword = '';
				showPasswordChange = false;
			} else {
				const data = await res.json();
				error = data.detail || 'Failed to change password';
			}
		} catch (err) {
			error = 'Failed to change password';
		} finally {
			saving = false;
		}
	}

	onMount(() => {
		loadSettings();
	});
</script>

<div class="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 p-8">
	<div class="max-w-2xl mx-auto">
		<!-- Header -->
		<div class="flex items-center justify-between mb-8">
			<h1 class="text-3xl font-bold text-white">Settings</h1>
			<button
				onclick={() => goto('/')}
				class="px-4 py-2 bg-slate-700 hover:bg-slate-600 text-white rounded-lg transition-colors"
			>
				← Back to Home
			</button>
		</div>

		{#if loading}
			<div class="bg-slate-800 rounded-xl p-8 text-center">
				<div class="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500 mx-auto"></div>
				<p class="text-slate-400 mt-4">Loading settings...</p>
			</div>
		{:else}
			<!-- Messages -->
			{#if message}
				<div class="bg-green-900/50 border border-green-500 text-green-200 px-4 py-3 rounded-lg mb-6">
					{message}
				</div>
			{/if}

			{#if error}
				<div class="bg-red-900/50 border border-red-500 text-red-200 px-4 py-3 rounded-lg mb-6">
					{error}
				</div>
			{/if}

			<!-- Authentication Settings -->
			<div class="bg-slate-800 rounded-xl p-6 mb-6">
				<h2 class="text-xl font-semibold text-white mb-4">Authentication</h2>

				<div class="space-y-4">
					<!-- Enable/Disable Auth -->
					<div class="flex items-center justify-between">
						<label class="text-slate-300">
							<span class="font-medium">Require Authentication</span>
							<p class="text-sm text-slate-400 mt-1">When enabled, users must log in to access the app</p>
						</label>
						<button
							onclick={() => (authEnabled = !authEnabled)}
							class="relative inline-flex h-6 w-11 items-center rounded-full transition-colors {authEnabled
								? 'bg-blue-600'
								: 'bg-slate-600'}"
						>
							<span
								class="inline-block h-4 w-4 transform rounded-full bg-white transition-transform {authEnabled
									? 'translate-x-6'
									: 'translate-x-1'}"
							></span>
						</button>
					</div>

					{#if authEnabled}
						<!-- Username -->
						<div>
							<label class="block text-slate-300 mb-2">Username</label>
							<input
								type="text"
								bind:value={username}
								placeholder="admin"
								class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg text-white placeholder-slate-400 focus:outline-none focus:border-blue-500"
							/>
						</div>

						<!-- New Password (optional when updating settings) -->
						<div>
							<label class="block text-slate-300 mb-2">
								New Password
								<span class="text-sm text-slate-400">(leave blank to keep current)</span>
							</label>
							<input
								type="password"
								bind:value={newPassword}
								placeholder="Enter new password"
								class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg text-white placeholder-slate-400 focus:outline-none focus:border-blue-500"
							/>
						</div>

						{#if newPassword}
							<div>
								<label class="block text-slate-300 mb-2">Confirm New Password</label>
								<input
									type="password"
									bind:value={confirmPassword}
									placeholder="Confirm password"
									class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg text-white placeholder-slate-400 focus:outline-none focus:border-blue-500"
								/>
							</div>
						{/if}
					{/if}

					<!-- Save Button -->
					<button
						onclick={saveSettings}
						disabled={saving}
						class="w-full px-6 py-3 bg-blue-600 hover:bg-blue-700 disabled:bg-slate-600 disabled:cursor-not-allowed text-white font-medium rounded-lg transition-colors"
					>
						{saving ? 'Saving...' : 'Save Settings'}
					</button>
				</div>
			</div>

			<!-- Change Password (separate section for existing auth) -->
			{#if authEnabled}
				<div class="bg-slate-800 rounded-xl p-6">
					<h2 class="text-xl font-semibold text-white mb-4">Change Password</h2>

					{#if !showPasswordChange}
						<button
							onclick={() => (showPasswordChange = true)}
							class="px-4 py-2 bg-slate-700 hover:bg-slate-600 text-white rounded-lg transition-colors"
						>
							Change Password
						</button>
					{:else}
						<div class="space-y-4">
							<div>
								<label class="block text-slate-300 mb-2">Current Password</label>
								<input
									type="password"
									bind:value={currentPassword}
									placeholder="Enter current password"
									class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg text-white placeholder-slate-400 focus:outline-none focus:border-blue-500"
								/>
							</div>

							<div>
								<label class="block text-slate-300 mb-2">New Password</label>
								<input
									type="password"
									bind:value={changePassword}
									placeholder="Enter new password"
									class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg text-white placeholder-slate-400 focus:outline-none focus:border-blue-500"
								/>
							</div>

							<div>
								<label class="block text-slate-300 mb-2">Confirm New Password</label>
								<input
									type="password"
									bind:value={changeConfirmPassword}
									placeholder="Confirm new password"
									class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg text-white placeholder-slate-400 focus:outline-none focus:border-blue-500"
								/>
							</div>

							<div class="flex gap-3">
								<button
									onclick={handlePasswordChange}
									disabled={saving}
									class="flex-1 px-6 py-3 bg-blue-600 hover:bg-blue-700 disabled:bg-slate-600 disabled:cursor-not-allowed text-white font-medium rounded-lg transition-colors"
								>
									{saving ? 'Changing...' : 'Change Password'}
								</button>
								<button
									onclick={() => {
										showPasswordChange = false;
										currentPassword = '';
										changePassword = '';
										changeConfirmPassword = '';
									}}
									class="px-6 py-3 bg-slate-700 hover:bg-slate-600 text-white rounded-lg transition-colors"
								>
									Cancel
								</button>
							</div>
						</div>
					{/if}
				</div>
			{/if}

			<!-- Default Presets -->
			<div class="bg-slate-800 rounded-xl p-6 mb-6">
				<h2 class="text-xl font-semibold text-white mb-4">Default Presets</h2>
				<p class="text-sm text-slate-400 mb-4">
					Set default values that will load automatically when the app starts
				</p>

				<div class="space-y-4">
					<!-- Target Size -->
					<div>
						<label class="block text-slate-300 mb-2">Default Target Size (MB)</label>
						<input
							type="number"
							bind:value={targetMB}
							min="1"
							class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg text-white placeholder-slate-400 focus:outline-none focus:border-blue-500"
						/>
					</div>

					<div class="grid grid-cols-1 md:grid-cols-2 gap-4">
						<!-- Video Codec -->
						<div>
							<label class="block text-slate-300 mb-2">Video Codec</label>
							<select
								bind:value={videoCodec}
								class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-blue-500"
							>
								<option value="av1_nvenc">AV1 (Best Quality)</option>
								<option value="hevc_nvenc">HEVC (H.265)</option>
								<option value="h264_nvenc">H.264 (Most Compatible)</option>
							</select>
						</div>

						<!-- Audio Codec -->
						<div>
							<label class="block text-slate-300 mb-2">Audio Codec</label>
							<select
								bind:value={audioCodec}
								class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-blue-500"
							>
								<option value="libopus">Opus (Default)</option>
								<option value="aac">AAC</option>
							</select>
						</div>

						<!-- Preset -->
						<div>
							<label class="block text-slate-300 mb-2">Speed/Quality Preset</label>
							<select
								bind:value={preset}
								class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-blue-500"
							>
								<option value="p1">P1 (Fastest)</option>
								<option value="p2">P2</option>
								<option value="p3">P3</option>
								<option value="p4">P4</option>
								<option value="p5">P5</option>
								<option value="p6">P6 (Balanced)</option>
								<option value="p7">P7 (Best Quality)</option>
							</select>
						</div>

						<!-- Audio Bitrate -->
						<div>
							<label class="block text-slate-300 mb-2">Audio Bitrate (kbps)</label>
							<select
								bind:value={audioKbps}
								class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-blue-500"
							>
								<option value={64}>64</option>
								<option value={96}>96</option>
								<option value={128}>128 (Default)</option>
								<option value={160}>160</option>
								<option value={192}>192</option>
								<option value={256}>256</option>
							</select>
						</div>

						<!-- Container -->
						<div>
							<label class="block text-slate-300 mb-2">Container</label>
							<select
								bind:value={container}
								class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-blue-500"
							>
								<option value="mp4">MP4 (Most Compatible)</option>
								<option value="mkv">MKV</option>
							</select>
						</div>

						<!-- Tune -->
						<div>
							<label class="block text-slate-300 mb-2">Tune</label>
							<select
								bind:value={tune}
								class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-blue-500"
							>
								<option value="hq">High Quality (HQ)</option>
								<option value="ll">Low Latency</option>
								<option value="ull">Ultra Low Latency</option>
								<option value="lossless">Lossless</option>
							</select>
						</div>
					</div>

					<!-- Save Presets Button -->
					<button
						onclick={savePresets}
						disabled={saving}
						class="w-full px-6 py-3 bg-green-600 hover:bg-green-700 disabled:bg-slate-600 disabled:cursor-not-allowed text-white font-medium rounded-lg transition-colors"
					>
						{saving ? 'Saving...' : 'Save Default Presets'}
					</button>
				</div>
			</div>

			<!-- How to Run (if it doesn't just work) -->
			<div class="bg-slate-800 rounded-xl p-4 mb-6 text-center">
				<button
					onclick={() => (showRunHelp = !showRunHelp)}
					class="w-full flex items-center justify-center gap-2 text-center"
				>
					<h2 class="text-lg font-semibold text-white">How to run (if it doesn't just work)</h2>
					<svg
						class="w-5 h-5 text-slate-400 transition-transform {showRunHelp ? 'rotate-180' : ''}"
						fill="none"
						stroke="currentColor"
						viewBox="0 0 24 24"
					>
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
					</svg>
				</button>

				{#if showRunHelp}
					<div class="mt-4 pt-4 border-t border-slate-700 text-left space-y-5">
						<p class="text-slate-300 text-sm">
							The app is a single Docker container exposing <span class="font-mono">http://localhost:8000</span>.
							If hardware acceleration isn't available, it will automatically fall back to CPU.
						</p>

						<!-- Windows -->
						<div>
							<h3 class="text-white font-medium mb-2">Windows (PowerShell)</h3>
							<p class="text-slate-400 text-sm mb-2">CPU‑only (works on all systems):</p>
							<pre class="bg-slate-900/70 border border-slate-700 rounded-lg p-3 overflow-auto text-xs text-slate-200">
docker run -d -p 8000:8000 ^
  -v ${PWD}\uploads:/app/uploads ^
  -v ${PWD}\outputs:/app/outputs ^
  --name 8mblocal jms1717/8mblocal:latest
							</pre>
							<p class="text-slate-400 text-sm mt-3 mb-2">NVIDIA GPU (Docker Desktop + WSL2 with GPU enabled):</p>
							<pre class="bg-slate-900/70 border border-slate-700 rounded-lg p-3 overflow-auto text-xs text-slate-200">
docker run -d -p 8000:8000 ^
  -v ${PWD}\uploads:/app/uploads ^
  -v ${PWD}\outputs:/app/outputs ^
  --gpus all ^
  --name 8mblocal jms1717/8mblocal:latest
							</pre>
							<p class="text-slate-500 text-xs mt-2">Note: Intel iGPU acceleration via WSL2 isn’t reliable; use CPU‑only on Windows when NVIDIA is unavailable.</p>
						</div>

						<!-- Linux -->
						<div>
							<h3 class="text-white font-medium mb-2">Linux</h3>
							<p class="text-slate-400 text-sm mb-2">NVIDIA (requires NVIDIA drivers + Container Toolkit):</p>
							<pre class="bg-slate-900/70 border border-slate-700 rounded-lg p-3 overflow-auto text-xs text-slate-200">
docker run -d -p 8000:8000 \
  -v $(pwd)/uploads:/app/uploads \
  -v $(pwd)/outputs:/app/outputs \
  --gpus all \
  --name 8mblocal jms1717/8mblocal:latest
							</pre>
							<p class="text-slate-400 text-sm mt-3 mb-2">Intel/AMD (VAAPI via /dev/dri):</p>
							<pre class="bg-slate-900/70 border border-slate-700 rounded-lg p-3 overflow-auto text-xs text-slate-200">
docker run -d -p 8000:8000 \
  -v $(pwd)/uploads:/app/uploads \
  -v $(pwd)/outputs:/app/outputs \
  --device /dev/dri:/dev/dri \
  --name 8mblocal jms1717/8mblocal:latest
							</pre>
						</div>

						<!-- macOS -->
						<div>
							<h3 class="text-white font-medium mb-2">macOS (CPU‑only)</h3>
							<pre class="bg-slate-900/70 border border-slate-700 rounded-lg p-3 overflow-auto text-xs text-slate-200">
docker run -d -p 8000:8000 \
  -v $(pwd)/uploads:/app/uploads \
  -v $(pwd)/outputs:/app/outputs \
  --name 8mblocal jms1717/8mblocal:latest
							</pre>
							<p class="text-slate-500 text-xs mt-2">macOS doesn’t support GPU passthrough to Docker; CPU encoders will be used automatically.</p>
						</div>

						<p class="text-slate-400 text-xs">
							Tip: The frontend is baked into the image. If you change the UI, rebuild the image to see updates.
						</p>
					</div>
				{/if}
			</div>

			<!-- Support Section -->
			<div class="bg-slate-800 rounded-xl p-4 text-center">
				<button
					onclick={() => (showSupport = !showSupport)}
					class="w-full flex items-center justify-center gap-2 text-center"
				>
					<h2 class="text-lg font-semibold text-white">Support the Project</h2>
					<svg
						class="w-5 h-5 text-slate-400 transition-transform {showSupport ? 'rotate-180' : ''}"
						fill="none"
						stroke="currentColor"
						viewBox="0 0 24 24"
					>
						<path
							stroke-linecap="round"
							stroke-linejoin="round"
							stroke-width="2"
							d="M19 9l-7 7-7-7"
						/>
					</svg>
				</button>

				{#if showSupport}
					<div class="mt-4 pt-4 border-t border-slate-700 space-y-4">
						<p class="text-slate-300 text-sm">
							If you find 8mb.local useful, consider supporting its development. Your support helps
							maintain the project and add new features!
						</p>

						<div class="flex flex-col gap-3 max-w-md mx-auto">
							<!-- PayPal Link -->
							<a
								href="https://www.paypal.me/jms1717"
								target="_blank"
								rel="noopener noreferrer"
								class="flex items-center justify-center gap-2 px-6 py-3 bg-blue-600 hover:bg-blue-700 text-white font-medium rounded-lg transition-colors"
							>
								<svg class="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
									<path
										d="M20.067 8.478c.492.88.556 2.014.3 3.327-.74 3.806-3.276 5.12-6.514 5.12h-.5a.805.805 0 00-.795.68l-.04.22-.63 3.993-.032.17a.804.804 0 01-.794.679H7.72a.483.483 0 01-.477-.558L7.418 21h1.518l.95-6.02h1.385c4.678 0 7.75-2.203 8.796-6.502z"
									/>
									<path
										d="M2.197 21.075a.484.484 0 01-.478-.558L3.05 9.935a.805.805 0 01.795-.681h4.97c1.194 0 2.024.232 2.62.61.555.352.98.845 1.28 1.44.315.627.485 1.406.485 2.326 0 .993-.214 1.914-.627 2.74-.394.788-.96 1.453-1.663 1.94-.665.462-1.477.792-2.41.962-.884.16-1.895.24-3.013.24H3.073a.483.483 0 00-.478.558l-.398 2.005z"
									/>
								</svg>
								Support via PayPal
							</a>

							<!-- GitHub Star -->
							<a
								href="https://github.com/JMS1717/8mb.local"
								target="_blank"
								rel="noopener noreferrer"
								class="flex items-center justify-center gap-2 px-6 py-3 bg-slate-700 hover:bg-slate-600 text-white font-medium rounded-lg transition-colors"
							>
								<svg class="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
									<path
										d="M12 0C5.374 0 0 5.373 0 12c0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23A11.509 11.509 0 0112 5.803c1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576C20.566 21.797 24 17.3 24 12c0-6.627-5.373-12-12-12z"
									/>
								</svg>
								Star on GitHub
							</a>
						</div>

						<p class="text-xs text-slate-500 text-center">
							Thank you for your support! ❤️
						</p>
					</div>
				{/if}
			</div>
		{/if}
	</div>
</div>
