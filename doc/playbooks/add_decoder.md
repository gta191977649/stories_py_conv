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
   - texture payload start and size
   - palette alpha mapping
6. Add tests for:
   - successful decode
   - failure behavior
   - parity with known reference math when applicable
7. Run the full test suite.

Validation:
- decoded geometry or texture is deterministic
- existing decode paths still pass
- streamed exports still respect resource variant selection
- streamed texture decode keeps the raw resource pointer when the header encodes a local raster offset
- streamed palette reads stop at the minimal raster block, not the end of an oversized resource blob

Do not:
- replace an existing decoder path with a broader heuristic unless regression coverage proves parity
- key caches too coarsely
- assume a streamed texture blob is only `header + texels + palette` when the source format can append unrelated data after the raster block
