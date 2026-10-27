# mitosis carries byte-for-byte copies of the shared metrics writer and price table

Status: accepted

mitosis writes the shared metrics format through metrics_writer.py, an unedited copy of the reference writer, held to copies of the shared fixtures by a conformance test that also checks each copy's SHA-256 against the source it was taken from, and prices spend with its own copy of the price table. mitosis must work installed alone, so it depends on no other plugin or shared package. We chose copies checked by tests over importing a shared module, which would tie every install of mitosis to another tool being present; the cost is that a new reference release reaches mitosis only when its copies are refreshed.
