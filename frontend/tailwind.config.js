/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        brand: {
          50: '#eef9ff',
          100: '#d8f1ff',
          200: '#b9e8ff',
          300: '#89dbff',
          400: '#51c5ff',
          500: '#29a7ff',
          600: '#1189fc',
          700: '#0a6fe8',
          800: '#0f59bb',
          900: '#134d93',
          950: '#112f59',
        },
        profit: '#22c55e',
        loss: '#ef4444',
        surface: {
          0: 'rgb(var(--surface-0) / <alpha-value>)',
          1: 'rgb(var(--surface-1) / <alpha-value>)',
          2: 'rgb(var(--surface-2) / <alpha-value>)',
          3: 'rgb(var(--surface-3) / <alpha-value>)',
          4: 'rgb(var(--surface-4) / <alpha-value>)',
        },
      },
    },
  },
  plugins: [],
};
