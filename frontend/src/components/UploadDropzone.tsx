import { FileArchive, UploadCloud } from "lucide-react";
import { useRef, useState } from "react";

import { formatBytes } from "../lib/format";

interface UploadDropzoneProps {
  file: File | null;
  onFile: (file: File | null) => void;
  maxMb: number;
  error?: string | null;
}

export function UploadDropzone({ file, onFile, maxMb, error }: UploadDropzoneProps) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  const pick = (files: FileList | null) => onFile(files?.[0] ?? null);

  return (
    <div>
      <button
        type="button"
        onClick={() => input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          pick(e.dataTransfer.files);
        }}
        className={`flex w-full flex-col items-center gap-2 rounded-xl border-2 border-dashed px-4 py-8 text-center transition-colors ${
          dragging
            ? "border-accent bg-accent/5"
            : error
              ? "border-bad/60"
              : "border-line hover:border-line-strong"
        }`}
      >
        {file ? (
          <>
            <FileArchive className="h-8 w-8 text-accent" aria-hidden />
            <span className="max-w-full truncate text-sm font-medium">{file.name}</span>
            <span className="text-xs text-muted">{formatBytes(file.size)} · click to change</span>
          </>
        ) : (
          <>
            <UploadCloud className="h-8 w-8 text-subtle" aria-hidden />
            <span className="text-sm font-medium">Drop a .zip here or click to browse</span>
            <span className="text-xs text-muted">
              Up to {maxMb} MB · project files at the root or in one folder
            </span>
          </>
        )}
      </button>
      <input
        ref={input}
        type="file"
        accept=".zip,application/zip"
        className="sr-only"
        data-testid="zip-input"
        onChange={(e) => pick(e.target.files)}
      />
      {error && <p className="field-error">{error}</p>}
    </div>
  );
}
