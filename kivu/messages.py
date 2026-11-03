"""Provider-neutral conversation types shared by every module.

To build:
- a message (role + content parts)
- content parts: text, tool call (id, name, arguments), tool result (id, output, is_error)
- token usage for one model call
Each provider converts to and from these, so the loop never sees a vendor format.
"""
