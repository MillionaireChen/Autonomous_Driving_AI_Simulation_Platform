/** @type {import('next').NextConfig} */
const nextConfig = {
  // Keep this a project-relative path.
  //
  // Next resolves distDir against the project directory, so an absolute path
  // does not escape the repo - it lands *inside* it. Pointing this at
  // /var/tmp/... produced frontend/var/tmp/fls/adarena/next-build, on the NFS
  // mount this setting exists to avoid, and 57 MB of build output was committed
  // before anyone noticed. scripts/fe-install.sh symlinks .next to local disk
  // instead, the same trick it already uses for node_modules.
  distDir: ".next",
  reactStrictMode: true,
};

export default nextConfig;
