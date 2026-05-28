export type FilePickerFilter = RegExp | ((file: File) => boolean) | undefined;
declare const _default: (startPath: string, includeFiles?: boolean, filter?: FilePickerFilter, filePickerSettings?: {
    validFileExtensions?: string[];
    defaultHidden?: boolean;
}) => Promise<{
    path: string;
    realpath: string;
}>;
export default _default;
