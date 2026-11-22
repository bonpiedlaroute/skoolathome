// Photos of the notebook sent with a request: made smaller on the phone before they leave it.
// 1024 pixels on the long side is enough for the model to read a page written by hand (measured
// on a printed handout completed in class), and makes a JPEG of about 200 KB: well under what
// the server takes in one request. Drawing it on a canvas also turns an iPhone's HEIC into JPEG.

export const MAX_PHOTOS = 6;
export const PHOTO_SIDE = 1024;
const QUALITY = 0.85;

/** The size of a picture brought within `side` pixels, its proportions kept; never enlarged. */
export function fitWithin(width: number, height: number, side = PHOTO_SIDE): { width: number; height: number } {
  const scale = Math.min(1, side / Math.max(width, height));
  return { width: Math.round(width * scale), height: Math.round(height * scale) };
}

/** The photo as a JPEG of at most `side` pixels. An <img> applies the camera's orientation. */
export async function shrink(file: File, side = PHOTO_SIDE): Promise<Blob> {
  const url = URL.createObjectURL(file);
  try {
    const image = new Image();
    image.src = url;
    await image.decode();
    const { width, height } = fitWithin(image.naturalWidth, image.naturalHeight, side);
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("canvas unavailable");
    context.drawImage(image, 0, 0, width, height);
    return await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("JPEG not made"))), "image/jpeg", QUALITY),
    );
  } finally {
    URL.revokeObjectURL(url);
  }
}
