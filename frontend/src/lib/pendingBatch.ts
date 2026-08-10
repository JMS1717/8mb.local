let pendingBatchFiles: File[] = [];

export function stagePendingBatchFiles(files: File[]): void {
	pendingBatchFiles = [...files];
}

export function takePendingBatchFiles(): File[] {
	const files = pendingBatchFiles;
	pendingBatchFiles = [];
	return files;
}
