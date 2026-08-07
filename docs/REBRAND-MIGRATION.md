# Boron namespace migration status

The checked-in application, installer, service definitions, configuration
templates, documentation, and production UI bundle now use only the Boron
namespace. The current installer is the supported path for a fresh host.

An installation built from an older product revision must be restored from a
versioned backup and migrated in an isolated maintenance environment using the
matching historical tooling. The current repository intentionally contains no
executable references to retired paths, service identities, or configuration
names.
