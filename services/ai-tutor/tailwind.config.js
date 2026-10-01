/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './lib/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      boxShadow: {
        panel: '0 20px 60px rgba(15, 23, 42, 0.28)',
      },
      keyframes: {
        pulseRing: {
          '0%, 100%': { transform: 'scale(1)', opacity: '0.7' },
          '50%': { transform: 'scale(1.08)', opacity: '1' },
        },
      },
      animation: {
        pulseRing: 'pulseRing 3s ease-in-out infinite',
      },
    },
  },
  plugins: [],
};
