import { getCourseChunksByVersion } from '@/lib/ai/db';

export type VectorStoreChunk = {
  id: string;
  material_id: string;
  chunk_index: number;
  label: string;
  section?: string | null;
  text: string;
  token_estimate: number;
  embedding: number[];
};

export async function loadCourseVectorChunks(versionId: string): Promise<VectorStoreChunk[]> {
  const chunks = await getCourseChunksByVersion(versionId);
  return chunks.map((chunk) => ({
    id: chunk.id,
    material_id: chunk.material_id,
    chunk_index: chunk.chunk_index,
    label: chunk.label,
    section: chunk.section ?? null,
    text: chunk.text,
    token_estimate: chunk.token_estimate,
    embedding: JSON.parse(chunk.embedding_json) as number[],
  }));
}
