<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';

	interface AuthSettings {
		auth_enabled: boolean;
		auth_user: string | null;
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

	async function loadSettings() {
		try {
			const res = await fetch('/api/settings/auth');
			if (res.ok) {
				const data: AuthSettings = await res.json();
				authEnabled = data.auth_enabled;
				username = data.auth_user || 'admin';
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
		{/if}
	</div>
</div>
