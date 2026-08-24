# Load optional overflow destinations from configured factories

**Superseded by [ADR 0002](0002-discovered-overflow-adapters.md):** the
catalog now discovers Adapters by scanning the adapters package instead of
reading factory paths from configuration.

Overflow delivery is optional and deployment-specific, so the bot loads each
enabled Adapter from the full `module:create` factory path in configuration
instead of hard-coding storage backends or introducing a DI container. Every
factory owns and validates its prefixed environment settings; successfully
loaded Adapter instances implement the fixed `OverflowDestination` interface
and appear automatically in the Owner's Menu.

The Owner selects one destination for the whole bot, and that selection is
persisted independently of deployment configuration. Missing and
misconfigured Adapter factories are retained as visible states rather than
silently replaced or allowed to stop startup: Chat delivery continues, while
an oversized Request names the problem and asks the Owner to choose another
destination.
