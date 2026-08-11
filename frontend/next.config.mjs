/** @type {import('next').NextConfig} */
const nextConfig = {
  // The build output goes to local disk: .next is thousands of small files and
  // the repo is on NFS, where that is painfully slow.
  distDir: process.env.ARENA_NEXT_DIST ?? ".next",
  reactStrictMode: true,
};

export default nextConfig;
