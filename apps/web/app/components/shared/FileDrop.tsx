"use client";

import { useId } from "react";
import { bytes } from "../../lib/format";

export const ACCEPT = ".pdf,.png,.jpg,.jpeg,.webp";

/** Keyboard-accessible file picker (the input stays focusable, only visually hidden). */
export function FileDrop({
  file,
  onChange,
  disabled,
  maxMb = 15,
}: {
  file: File | null;
  onChange: (file: File | null) => void;
  disabled?: boolean;
  maxMb?: number;
}) {
  const id = useId();
  return (
    <label className={`dropzone ${disabled ? "disabled" : ""}`} htmlFor={id}>
      <input
        id={id}
        className="visuallyHidden"
        type="file"
        accept={ACCEPT}
        disabled={disabled}
        onChange={(event) => onChange(event.target.files?.[0] ?? null)}
      />
      <span className="dropTitle">{file ? file.name : "Choose PDF or image"}</span>
      <span className="dropMeta">
        {file ? bytes(file.size) : `PDF · PNG · JPG · WEBP · max ${maxMb} MB`}
      </span>
    </label>
  );
}
