# Immutable frontend release contract

`deploy.frontend_release.build_frontend(source: Path, destination: Path) -> Path`
returns the absolute destination path. The destination **must not exist**, and
must be separate from the source (neither ancestor nor descendant). Build in a
fresh private candidate directory, then pass that directory to `publish_assets`.
No service, network, origin configuration, CDN configuration, or controller
changes are made by the builder.

- The source is validated with the publisher's `public_tree`: index required;
  only its public extension allowlist; no hidden, symlink, hardlink or special
  files. URL-ambiguous names and case-colliding path components are rejected.
- A deterministic 24-hex version is SHA-256 over a builder format marker plus
  sorted, length-prefixed UTF-8 relative paths and complete source bytes.
  A change to *any* source file changes *every* hashed asset URL, including all
  transitive JS modules, CSS, manifest, icons and the service worker.
- `index.html` is stable. Every other source file becomes
  `directory/stem.<version>.extension`. Binary assets are copied byte-for-byte.
  Output contains exactly one file per input, with no public metadata JSON.
  Mapping is computed in memory; derive the version from any emitted filename.
- Text references to known assets are rewritten in HTML, CSS, JS, MJS,
  webmanifest and SVG. Supported source spelling is the application's literal
  `/hermes/<path>`, or relative paths from the referencing file (including `./`
  and `../`). Query strings/fragments are retained. This is a literal static
  graph builder, not a JavaScript evaluator or general-purpose bundler:
  dynamically assembled/escaped asset paths are not supported.
- The `const CACHE='hermes-public-…'` declaration in root `sw.js` becomes
  `hermes-public-<version>`; its cleanup prefix remains unchanged. Registration,
  precache lists, manifest icons and UI brand URLs use the same version.
  Other runtime behavior, API routes, scopes, start URLs and external URLs
  remain unchanged.
- All validation and text transformation occur before destination creation.
  Existing destinations are refused, never merged or overwritten. The caller
  should remove a partial candidate on an OS-level write failure. Publication
  and rollback remain responsibilities of the existing publisher/controller.

## Integration constraints

Serve `/hermes/` and `/hermes/index.html` with CDN cache bypass; this builder
cannot fix an already cached HTML entry point. Hashed asset URLs must never be
reused for different content. A fresh build does not retain previous release
files; retaining old versions for already-open tabs is a separate publisher
policy. Bump the builder's format marker if its transformation semantics change.

Tests use temporary fixtures only and execute a versioned module dependency
chain with Node. Browser delivery/registration smoke tests belong to the parent
controller integration.
