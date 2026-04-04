# Add Decoder

Goal:
- Introduce or extend a binary decode path without breaking existing extraction behavior.

Procedure:
1. Identify which path is being extended:
   - standard IMG model/texture path
   - streamed packet path
   - MDL-style fallback path
2. Find the existing caller and expected output structure.
3. Preserve source provenance and candidate identity if this is a streamed resource.
4. Implement the decoder behind the current selection pipeline instead of bypassing it.
5. Keep numeric decode rules explicit:
   - position scaling
   - UV scaling
   - color/prelight extraction
6. Add tests for:
   - successful decode
   - failure behavior
   - parity with known reference math when applicable
7. Run the full test suite.

Validation:
- decoded geometry or texture is deterministic
- existing decode paths still pass
- streamed exports still respect resource variant selection

Do not:
- replace an existing decoder path with a broader heuristic unless regression coverage proves parity
- key caches too coarsely
