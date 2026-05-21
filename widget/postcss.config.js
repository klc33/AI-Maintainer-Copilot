// We don't use Tailwind for the widget anymore — vanilla CSS keeps the
// bundle small enough that Tailwind's overhead isn't justified. autoprefixer
// is still useful for vendor prefixes on a few flex/grid properties.
export default {
  plugins: {
    autoprefixer: {},
  },
};
