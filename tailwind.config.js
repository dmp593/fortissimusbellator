module.exports = {
  content: [
    './templates/**/*.html',
    './**/templates/**/*.html',
    '!../node_modules',
  ],
  theme: {
    extend: {
      fontFamily: {
        serif: ['Playfair Display', 'serif'],
        sans: ['Poppins', 'sans-serif'],
      }
  },
  plugins: [],
}
