import adapter from '@sveltejs/adapter-static';

const config = {
  compilerOptions: {
    // Svelte 5 handling for warnings if they moved here
  },
  onwarn: (warning, handler) => {
    if (warning.code.startsWith('a11y_')) return;
    if (warning.code === 'css_unused_selector') return;
    handler(warning);
  },
  kit: {
    adapter: adapter({
      pages: 'build',
      assets: 'build',
      fallback: 'index.html',
      precompress: false
    }),
    alias: {
      $lib: 'src/lib'
    }
  }
};

export default config;
