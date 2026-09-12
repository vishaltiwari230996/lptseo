/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The floating dev-tools badge sits top-right where the stats bar's
  // notification bell lives — hide it so it can't swallow clicks in dev.
  devIndicators: false,
};

export default nextConfig;
