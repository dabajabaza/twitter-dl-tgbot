# Discover overflow adapters by scanning the adapters package

Supersedes [ADR 0001](0001-configured-overflow-adapters.md). Requiring one
`OVERFLOW_ADAPTERS__*` line per Adapter meant a deployment without those lines
showed an empty Menu with nothing to select and no hint why. The catalog now
scans `clipivore/adapters/` at startup: every non-underscore module holding
exactly one concrete `OverflowDestination` subclass is an Adapter, its file
name is the stable id, its `label` class attribute names it in Menu, and it is
constructed with no arguments — settings still come from the subclass's own
prefixed environment variables. `OVERFLOW_ADAPTERS__*` and `OVERFLOW_DEFAULT`
are gone; with no persisted choice Overflow delivery starts off.

The trade-off is the extension point: an Adapter now has to be a module inside
the package, so out-of-repo `module:create` factories are no longer possible.
Accepted deliberately — this bot has one deployer, and adding a file to the
package is the same effort as an env line was. Everything else from ADR 0001
stands: the Owner selects one destination for the whole bot, persisted
independently of deployment configuration; a module that fails to import,
construct or shape up is retained as a visible misconfigured state rather than
silently dropped or allowed to stop startup — named by its class label when a
single class with a usable label imported, by its file name otherwise. A
subdirectory in the package is likewise a visible misconfigured entry, not a
silent skip.
