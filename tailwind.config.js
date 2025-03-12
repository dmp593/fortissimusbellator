module.exports = {
  plugins: [
    require("@designbycode/tailwindcss-text-stroke")
  ],
  content: [
    './templates/**/*.html',
    './**/templates/**/*.html',
    './assets/js/*.js',
    './assets/js/**/*.js',
    './**/assets/js/**/*.js',
    '!../node_modules',
  ],
  plugins: [],
};
