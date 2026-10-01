import fs from 'fs';
import path from 'path';

/**
 * Manually parses .env.local and .env files to extract and force load
 * the local OPENAI_API_KEY, overriding any system-wide environment variables.
 */
function forceLocalOpenAIKey() {
  const envFiles = ['.env.local', '.env'];

  for (const file of envFiles) {
    const fullPath = path.join(process.cwd(), file);
    if (fs.existsSync(fullPath)) {
      try {
        const content = fs.readFileSync(fullPath, 'utf-8');
        // Matches key = value, optional quotes
        const match = content.match(/^OPENAI_API_KEY\s*=\s*(["']?)(.*?)\1\s*$/m);
        if (match && match[2]) {
          const localKey = match[2].trim();
          if (localKey) {
            process.env.OPENAI_API_KEY = localKey;
            console.log(`[Env Override] Successfully loaded and forced local OPENAI_API_KEY from ${file}`);
            return;
          }
        }
      } catch (err) {
        console.error(`[Env Override] Error reading/parsing ${file}:`, err);
      }
    }
  }
}

// Execute immediately when imported
forceLocalOpenAIKey();
