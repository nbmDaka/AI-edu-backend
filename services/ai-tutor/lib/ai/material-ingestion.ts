import { createHash, randomUUID } from 'node:crypto';
import mammoth from 'mammoth';
import JSZip from 'jszip';
import pdfParse from 'pdf-parse';
import OpenAI from 'openai';
import { findExistingMaterialByHash, insertCourseChunk, insertCourseMaterial } from '@/lib/ai/db';
import { estimateTokens } from '@/lib/ai/request';

export const INDEX_EMBEDDING_MODEL = process.env.EMBEDDING_MODEL ?? 'text-embedding-3-small';
const LOCAL_STT_URL = process.env.LOCAL_STT_URL ?? 'http://127.0.0.1:8001/transcribe';
const MATERIAL_MAX_BYTES = Number(process.env.AI_MATERIAL_MAX_BYTES ?? 50 * 1024 * 1024);
const TARGET_CHUNK_CHARS = Number(process.env.AI_INGEST_CHUNK_TARGET_CHARS ?? 1200);
const MAX_CHUNK_CHARS = Number(process.env.AI_INGEST_CHUNK_MAX_CHARS ?? 1700);
const VIDEO_ALLOWED_TYPES = new Set([
  'video/mp4',
  'video/webm',
  'video/quicktime',
  'audio/wav',
  'audio/mpeg',
  'audio/webm',
  'audio/mp4',
  'audio/ogg',
  'audio/m4a',
]);

type BlockKind = 'heading' | 'paragraph' | 'list' | 'table' | 'code';

type StructuredBlock = {
  kind: BlockKind;
  text: string;
  heading?: string;
  level?: number;
};

type NonHeadingBlockKind = Exclude<BlockKind, 'heading'>;

type MaterialStructure = Record<string, unknown> & {
  block_count?: number;
  heading_count?: number;
  list_count?: number;
  table_count?: number;
  code_count?: number;
  section_titles?: string[];
};

export type ExtractedMaterial = {
  fileName: string;
  mediaType: string;
  sourceKind: 'pdf' | 'pptx' | 'text' | 'docx' | 'video';
  sourceHash: string;
  structure: MaterialStructure;
  text: string;
  blocks: StructuredBlock[];
};

export type ChunkRecord = {
  label: string;
  text: string;
  section?: string;
};

function fileExtension(name: string): string {
  const parts = name.toLowerCase().split('.');
  return parts.length > 1 ? parts.pop() ?? '' : '';
}

async function hashFile(file: File): Promise<string> {
  const buffer = Buffer.from(await file.arrayBuffer());
  return createHash('sha256').update(buffer).digest('hex');
}

function normalizeText(input: string): string {
  return input
    .replace(/\r\n/g, '\n')
    .replace(/\r/g, '\n')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n[ \t]+/g, '\n')
    .replace(/[ \t]{2,}/g, ' ')
    .replace(/\u00a0/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function decodeXmlEntities(input: string): string {
  return input
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&#xA;/gi, '\n');
}

function stripHtml(input: string): string {
  return normalizeText(
    decodeXmlEntities(
      input
        .replace(/<br\s*\/?>/gi, '\n')
        .replace(/<\/(p|div|section|article|li|ul|ol|h1|h2|h3|h4|h5|h6|tr|pre)>/gi, '\n\n')
        .replace(/<\/(td|th)>/gi, ' | ')
        .replace(/<[^>]+>/g, ' '),
    ),
  );
}

function markdownTableFromRows(rows: string[][]): string {
  if (!rows.length) {
    return '';
  }
  if (rows.length === 1) {
    return rows[0].join(' | ');
  }

  const header = rows[0];
  const separator = header.map(() => '---');
  const body = rows.slice(1);
  return [header.join(' | '), separator.join(' | '), ...body.map((row) => row.join(' | '))].join('\n');
}

function parseHtmlToBlocks(html: string): StructuredBlock[] {
  const blocks: StructuredBlock[] = [];
  const normalizedHtml = html
    .replace(/\r/g, '')
    .replace(/<pre[^>]*>\s*<code[^>]*>/gi, '<pre><code>')
    .replace(/<\/code>\s*<\/pre>/gi, '</code></pre>');
  const tokenRegex = /<(h[1-6]|p|li|pre|table)[^>]*>([\s\S]*?)<\/\1>/gi;
  let match: RegExpExecArray | null;
  let currentHeading: string | undefined;

  while ((match = tokenRegex.exec(normalizedHtml))) {
    const tag = match[1].toLowerCase();
    const inner = match[2];

    if (tag.startsWith('h')) {
      const text = stripHtml(inner);
      if (!text) {
        continue;
      }
      currentHeading = text;
      blocks.push({
        kind: 'heading',
        text,
        heading: text,
        level: Number(tag.slice(1)),
      });
      continue;
    }

    if (tag === 'p') {
      const text = stripHtml(inner);
      if (!text) {
        continue;
      }
      blocks.push({
        kind: 'paragraph',
        text,
        heading: currentHeading,
      });
      continue;
    }

    if (tag === 'li') {
      const text = stripHtml(inner);
      if (!text) {
        continue;
      }
      blocks.push({
        kind: 'list',
        text: `- ${text}`,
        heading: currentHeading,
      });
      continue;
    }

    if (tag === 'pre') {
      const code = stripHtml(inner);
      if (!code) {
        continue;
      }
      blocks.push({
        kind: 'code',
        text: `\`\`\`\n${code}\n\`\`\``,
        heading: currentHeading,
      });
      continue;
    }

    if (tag === 'table') {
      const rowMatches = [...inner.matchAll(/<tr[^>]*>([\s\S]*?)<\/tr>/gi)];
      const rows = rowMatches
        .map((rowMatch) => {
          return [...rowMatch[1].matchAll(/<(td|th)[^>]*>([\s\S]*?)<\/\1>/gi)]
            .map((cellMatch) => stripHtml(cellMatch[2]))
            .filter(Boolean);
        })
        .filter((row) => row.length > 0);

      const tableText = normalizeText(markdownTableFromRows(rows));
      if (!tableText) {
        continue;
      }
      blocks.push({
        kind: 'table',
        text: tableText,
        heading: currentHeading,
      });
    }
  }

  return blocks;
}

function parsePlainTextToBlocks(text: string): StructuredBlock[] {
  const normalized = normalizeText(text);
  if (!normalized) {
    return [];
  }

  const lines = normalized.split('\n');
  const blocks: StructuredBlock[] = [];
  const current: string[] = [];
  let currentKind: NonHeadingBlockKind = 'paragraph';
  let currentHeading: string | undefined;
  let inCodeFence = false;

  const flush = () => {
    const payload = current.join('\n').trim();
    if (!payload) {
      current.length = 0;
      return;
    }
    blocks.push({
      kind: currentKind,
      text: payload,
      heading: currentHeading,
    });
    current.length = 0;
    currentKind = 'paragraph';
  };

  const isTextHeading = (line: string) => {
    if (line.length > 140) {
      return false;
    }
    return (
      /^#{1,6}\s+/.test(line) ||
      /^\d+(\.\d+)*[.)]?\s+/.test(line) ||
      /^[A-ZА-ЯӘІҢҒҮҰҚӨҺ0-9][A-ZА-ЯӘІҢҒҮҰҚӨҺ0-9\s\-()]{5,}$/.test(line)
    );
  };

  const normalizeHeading = (line: string) => line.replace(/^#{1,6}\s+/, '').trim();
  const isListLine = (line: string) => /^[-*•]\s+/.test(line) || /^\d+[.)]\s+/.test(line);
  const isTableLine = (line: string) => /\|/.test(line) || /\t/.test(line) || /^;?[^;]+;[^;]+/.test(line);
  const isCodeLine = (line: string) =>
    /^(from |import |class |def |const |let |var |function |if |for |while |return |SELECT |INSERT |UPDATE |DELETE )/i.test(
      line.trim(),
    ) ||
    /[{}();]{2,}/.test(line) ||
    /^\s{4,}\S/.test(line);

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    const trimmed = line.trim();

    if (/^```/.test(trimmed)) {
      if (!inCodeFence) {
        flush();
        inCodeFence = true;
        currentKind = 'code';
        current.push('```');
      } else {
        current.push('```');
        flush();
        inCodeFence = false;
      }
      continue;
    }

    if (inCodeFence) {
      current.push(line);
      continue;
    }

    if (!trimmed) {
      flush();
      continue;
    }

    if (isTextHeading(trimmed)) {
      flush();
      const heading = normalizeHeading(trimmed);
      currentHeading = heading;
      blocks.push({
        kind: 'heading',
        text: heading,
        heading,
      });
      continue;
    }

    const nextKind: NonHeadingBlockKind = isListLine(trimmed)
      ? 'list'
      : isTableLine(trimmed)
        ? 'table'
        : isCodeLine(line)
          ? 'code'
          : 'paragraph';

    if (current.length && nextKind !== currentKind) {
      flush();
    }
    currentKind = nextKind;

    if (nextKind === 'table' && /\t/.test(line)) {
      current.push(line.replace(/\t+/g, ' | '));
    } else {
      current.push(line);
    }
  }

  flush();

  return blocks.map((block) => {
    if (block.kind !== 'code') {
      return {
        ...block,
        text: normalizeText(block.text),
      };
    }

    const codeText = block.text.startsWith('```') ? block.text : `\`\`\`\n${block.text}\n\`\`\``;
    return {
      ...block,
      text: codeText,
    };
  });
}

function summarizeBlocks(blocks: StructuredBlock[], extra: Record<string, unknown> = {}): MaterialStructure {
  const sectionTitles = blocks
    .filter((block) => block.kind === 'heading')
    .map((block) => block.text)
    .slice(0, 50);

  return {
    ...extra,
    block_count: blocks.length,
    heading_count: blocks.filter((block) => block.kind === 'heading').length,
    list_count: blocks.filter((block) => block.kind === 'list').length,
    table_count: blocks.filter((block) => block.kind === 'table').length,
    code_count: blocks.filter((block) => block.kind === 'code').length,
    section_titles: sectionTitles,
  };
}

function materialTextFromBlocks(blocks: StructuredBlock[]): string {
  return normalizeText(
    blocks
      .map((block) => block.text)
      .filter(Boolean)
      .join('\n\n'),
  );
}

function splitLargeBlock(text: string, maxChars: number): string[] {
  if (text.length <= maxChars) {
    return [text];
  }

  const paragraphs = text.split(/\n{2,}/).map((item) => item.trim()).filter(Boolean);
  if (paragraphs.length <= 1) {
    return text.match(new RegExp(`.{1,${maxChars}}`, 'g')) ?? [text];
  }

  const parts: string[] = [];
  let current = '';

  for (const paragraph of paragraphs) {
    const candidate = current ? `${current}\n\n${paragraph}` : paragraph;
    if (candidate.length > maxChars && current) {
      parts.push(current);
      current = paragraph;
      continue;
    }
    current = candidate;
  }

  if (current) {
    parts.push(current);
  }

  return parts;
}

function blockToChunkText(block: StructuredBlock, sectionLabel?: string) {
  if (block.kind === 'heading') {
    return block.text;
  }
  if (sectionLabel && !block.text.startsWith(sectionLabel)) {
    return `${sectionLabel}\n${block.text}`;
  }
  return block.text;
}

export function chunkByMeaningFromBlocks(blocks: StructuredBlock[], fallbackLabel: string): ChunkRecord[] {
  if (!blocks.length) {
    return [];
  }

  const chunks: ChunkRecord[] = [];
  let currentHeading = fallbackLabel;
  let currentParts: string[] = [];
  let sectionStart = fallbackLabel;
  let partIndex = 1;

  const flush = () => {
    const normalized = normalizeText(currentParts.join('\n\n'));
    if (!normalized) {
      currentParts = [];
      return;
    }

    const pieces = splitLargeBlock(normalized, MAX_CHUNK_CHARS).map((piece) => normalizeText(piece)).filter(Boolean);
    for (const piece of pieces) {
      const label = pieces.length > 1 || partIndex > 1 ? `${sectionStart} / part ${partIndex}` : sectionStart;
      chunks.push({
        label,
        section: sectionStart,
        text: piece,
      });
      partIndex += 1;
    }
    currentParts = [];
  };

  for (const block of blocks) {
    if (block.kind === 'heading') {
      flush();
      currentHeading = block.text;
      sectionStart = block.text || fallbackLabel;
      partIndex = 1;
      continue;
    }

    const nextText = blockToChunkText(block, currentHeading);
    const currentText = currentParts.join('\n\n');
    const candidate = currentText ? `${currentText}\n\n${nextText}` : nextText;
    const forceStandalone = block.kind === 'table' || block.kind === 'code';
    if ((candidate.length > TARGET_CHUNK_CHARS && currentParts.length) || (forceStandalone && currentParts.length)) {
      flush();
    }

    if (forceStandalone && nextText.length > MAX_CHUNK_CHARS) {
      const pieces = splitLargeBlock(nextText, MAX_CHUNK_CHARS);
      for (const piece of pieces) {
        chunks.push({
          label: `${sectionStart} / part ${partIndex}`,
          section: sectionStart,
          text: normalizeText(piece),
        });
        partIndex += 1;
      }
      continue;
    }

    currentParts.push(nextText);

    if (forceStandalone || currentParts.join('\n\n').length >= MAX_CHUNK_CHARS) {
      flush();
    }
  }

  flush();

  return chunks.length
    ? chunks
    : [
        {
          label: fallbackLabel,
          text: materialTextFromBlocks(blocks),
          section: fallbackLabel,
        },
      ];
}

export function chunkByMeaning(text: string, fallbackLabel: string): ChunkRecord[] {
  return chunkByMeaningFromBlocks(parsePlainTextToBlocks(text), fallbackLabel);
}

async function extractPdf(file: File): Promise<ExtractedMaterial> {
  const arrayBuffer = await file.arrayBuffer();
  const parsed = await pdfParse(Buffer.from(arrayBuffer), {
    pagerender: async (pageData: { getTextContent: () => Promise<{ items: Array<{ str?: string; transform?: number[] }> }> }) => {
      const content = await pageData.getTextContent();
      const rows: string[] = [];
      let currentY: number | null = null;
      let currentLine = '';

      for (const item of content.items as Array<{ str?: string; transform?: number[] }>) {
        const text = String(item.str ?? '').trim();
        if (!text) {
          continue;
        }
        const y = Array.isArray(item.transform) ? Number(item.transform[5]) : 0;
        if (currentY !== null && Math.abs(y - currentY) > 2) {
          if (currentLine.trim()) {
            rows.push(currentLine.trim());
          }
          currentLine = text;
        } else {
          currentLine = currentLine ? `${currentLine} ${text}` : text;
        }
        currentY = y;
      }

      if (currentLine.trim()) {
        rows.push(currentLine.trim());
      }

      return rows.join('\n');
    },
  });

  const text = normalizeText(parsed.text || '');
  const blocks = parsePlainTextToBlocks(text);
  return {
    fileName: file.name,
    mediaType: file.type || 'application/pdf',
    sourceKind: 'pdf',
    sourceHash: createHash('sha256').update(Buffer.from(arrayBuffer)).digest('hex'),
    structure: summarizeBlocks(blocks, {
      pages: parsed.numpages ?? null,
      info: parsed.info ?? null,
      extraction_mode: 'pdf-parse',
    }),
    text: materialTextFromBlocks(blocks),
    blocks,
  };
}

async function extractDocx(file: File): Promise<ExtractedMaterial> {
  const arrayBuffer = await file.arrayBuffer();
  const buffer = Buffer.from(arrayBuffer);
  const result = await mammoth.convertToHtml({ buffer });
  const html = result.value ?? '';
  const htmlBlocks = parseHtmlToBlocks(html);
  const fallbackBlocks = parsePlainTextToBlocks(
    normalizeText(
      html
        .replace(/<br\s*\/?>/gi, '\n')
        .replace(/<\/(p|li|h1|h2|h3|h4|h5|h6|div|tr|pre)>/gi, '\n\n')
        .replace(/<\/(td|th)>/gi, ' | ')
        .replace(/<[^>]+>/g, ' '),
    ),
  );
  const blocks = htmlBlocks.length ? htmlBlocks : fallbackBlocks;

  const zip = await JSZip.loadAsync(buffer);
  const documentXml = await zip.file('word/document.xml')?.async('string');
  const paragraphCount = documentXml ? [...documentXml.matchAll(/<w:p\b/gi)].length : undefined;
  const tableCount = documentXml ? [...documentXml.matchAll(/<w:tbl\b/gi)].length : undefined;

  return {
    fileName: file.name,
    mediaType: file.type || 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    sourceKind: 'docx',
    sourceHash: createHash('sha256').update(buffer).digest('hex'),
    structure: summarizeBlocks(blocks, {
      messages: result.messages,
      paragraph_count: paragraphCount ?? null,
      detected_tables: tableCount ?? 0,
      extraction_mode: 'mammoth-html',
    }),
    text: materialTextFromBlocks(blocks),
    blocks,
  };
}

async function extractPptx(file: File): Promise<ExtractedMaterial> {
  const fileBuffer = await file.arrayBuffer();
  const buffer = Buffer.from(fileBuffer);
  const zip = await JSZip.loadAsync(fileBuffer);
  const slideNames = Object.keys(zip.files)
    .filter((name) => /^ppt\/slides\/slide\d+\.xml$/i.test(name))
    .sort((a: any, b: any) => {
      const aNum = Number(a.match(/slide(\d+)/i)?.[1] ?? 0);
      const bNum = Number(b.match(/slide(\d+)/i)?.[1] ?? 0);
      return aNum - bNum;
    });

  const slides: Array<{ slide: number; title: string; text: string; notes: string }> = [];
  const blocks: StructuredBlock[] = [];

  for (const slideName of slideNames) {
    const xml = await zip.files[slideName]?.async('string');
    if (!xml) {
      continue;
    }
    const lines = [...xml.matchAll(/<a:t[^>]*>([\s\S]*?)<\/a:t>/gi)]
      .map((match) => decodeXmlEntities(match[1]))
      .map((value) => value.trim())
      .filter(Boolean);
    const slideNumber = Number(slideName.match(/slide(\d+)/i)?.[1] ?? slides.length + 1);
    const title = lines[0] || `Slide ${slideNumber}`;
    const bodyLines = lines.slice(1);

    const notesPath = `ppt/notesSlides/notesSlide${slideNumber}.xml`;
    const notesXml = await zip.file(notesPath)?.async('string');
    const notesLines = notesXml
      ? [...notesXml.matchAll(/<a:t[^>]*>([\s\S]*?)<\/a:t>/gi)]
          .map((match) => decodeXmlEntities(match[1]).trim())
          .filter(Boolean)
      : [];
    const notes = normalizeText(notesLines.join('\n'));

    slides.push({
      slide: slideNumber,
      title,
      text: normalizeText(bodyLines.join('\n')),
      notes,
    });

    blocks.push({
      kind: 'heading',
      text: `Slide ${slideNumber}: ${title}`,
      heading: `Slide ${slideNumber}: ${title}`,
      level: 1,
    });

    const bodyBlocks = parsePlainTextToBlocks(bodyLines.join('\n'));
    blocks.push(...bodyBlocks.map((block) => ({ ...block, heading: `Slide ${slideNumber}: ${title}` })));

    if (notes) {
      blocks.push({
        kind: 'paragraph',
        text: `Notes:\n${notes}`,
        heading: `Slide ${slideNumber}: ${title}`,
      });
    }
  }

  return {
    fileName: file.name,
    mediaType: file.type || 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    sourceKind: 'pptx',
    sourceHash: createHash('sha256').update(buffer).digest('hex'),
    structure: summarizeBlocks(blocks, {
      slides: slides.map((slide) => ({
        slide: slide.slide,
        title: slide.title,
        preview: slide.text.slice(0, 160),
        has_notes: slide.notes.length > 0,
      })),
      slide_count: slides.length,
      extraction_mode: 'pptx-xml',
    }),
    text: materialTextFromBlocks(blocks),
    blocks,
  };
}

async function extractPlainText(file: File): Promise<ExtractedMaterial> {
  const text = normalizeText(await file.text());
  const blocks = parsePlainTextToBlocks(text);
  return {
    fileName: file.name,
    mediaType: file.type || 'text/plain',
    sourceKind: 'text',
    sourceHash: await hashFile(file),
    structure: summarizeBlocks(blocks, {
      lines: text.split('\n').length,
      extraction_mode: 'plain-text',
    }),
    text: materialTextFromBlocks(blocks),
    blocks,
  };
}

async function extractVideoTranscript(file: File): Promise<ExtractedMaterial> {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('language', 'auto');

  const response = await fetch(LOCAL_STT_URL, {
    method: 'POST',
    body: formData,
  });
  if (!response.ok) {
    const body = await response.text().catch(() => '');
    throw new Error(`video transcript extraction failed: ${response.status} ${body}`.trim());
  }

  const payload = (await response.json()) as { text?: string; transcript?: string; transcription?: string; language?: string };
  const text = normalizeText(payload.text ?? payload.transcript ?? payload.transcription ?? '');
  const blocks = parsePlainTextToBlocks(text);
  return {
    fileName: file.name,
    mediaType: file.type || 'video/mp4',
    sourceKind: 'video',
    sourceHash: await hashFile(file),
    structure: summarizeBlocks(blocks, {
      transcript_language: payload.language ?? '',
      extraction_mode: 'stt-transcript',
    }),
    text: materialTextFromBlocks(blocks),
    blocks,
  };
}

export async function extractMaterial(file: File): Promise<ExtractedMaterial> {
  if (file.size <= 0) {
    throw new Error(`file ${file.name} is empty`);
  }
  if (file.size > MATERIAL_MAX_BYTES) {
    throw new Error(`file ${file.name} exceeds ${MATERIAL_MAX_BYTES} bytes`);
  }

  const ext = fileExtension(file.name);
  if (ext === 'pdf') {
    return extractPdf(file);
  }
  if (ext === 'pptx') {
    return extractPptx(file);
  }
  if (ext === 'docx') {
    return extractDocx(file);
  }
  if (['txt', 'md', 'csv'].includes(ext) || file.type.startsWith('text/')) {
    return extractPlainText(file);
  }
  if (VIDEO_ALLOWED_TYPES.has((file.type || '').split(';')[0].trim().toLowerCase())) {
    return extractVideoTranscript(file);
  }

  throw new Error(`unsupported material type: ${file.name}`);
}

export async function indexExtractedMaterials(params: {
  client: OpenAI;
  courseId: string;
  versionId: string;
  materials: ExtractedMaterial[];
}) {
  let totalEmbeddingTokens = 0;
  let chunkCount = 0;

  for (const material of params.materials) {
    if (
      await findExistingMaterialByHash({
        courseId: params.courseId,
        versionId: params.versionId,
        sourceHash: material.sourceHash,
      })
    ) {
      continue;
    }

    const materialId = randomUUID();
    await insertCourseMaterial({
      id: materialId,
      versionId: params.versionId,
      courseId: params.courseId,
      fileName: material.fileName,
      mediaType: material.mediaType,
      sourceKind: material.sourceKind,
      sourceHash: material.sourceHash,
      structure: material.structure,
      extractedText: material.text,
    });

    const chunks = chunkByMeaningFromBlocks(material.blocks, material.fileName);
    if (!chunks.length) {
      continue;
    }

    const embeddings = await params.client.embeddings.create({
      model: INDEX_EMBEDDING_MODEL,
      input: chunks.map((chunk) => chunk.text),
    });
    totalEmbeddingTokens += embeddings.usage?.total_tokens ?? 0;

    for (const [index, chunk] of chunks.entries()) {
      const embedding = embeddings.data[index]?.embedding;
      if (!embedding) {
        continue;
      }
      await insertCourseChunk({
        id: randomUUID(),
        versionId: params.versionId,
        courseId: params.courseId,
        materialId,
        chunkIndex: index,
        label: chunk.label || `${material.fileName} / chunk ${index + 1}`,
        section: chunk.section ?? null,
        text: chunk.text,
        tokenEstimate: estimateTokens(chunk.text),
        embedding,
      });
      chunkCount += 1;
    }
  }

  return {
    chunkCount,
    embeddingTokens: totalEmbeddingTokens,
  };
}
