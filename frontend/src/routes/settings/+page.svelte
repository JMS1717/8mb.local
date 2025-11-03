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
			<div class="bg-slate-800 rounded-xl p-6">
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
		{/if}
	</div>
</div>
