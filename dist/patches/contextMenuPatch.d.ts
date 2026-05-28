import { Patch } from '@decky/ui';
/**
 * Patches the game context menu.
 * @param LibraryContextMenu The game context menu.
 * @returns A patch to remove when the plugin dismounts.
 */
declare const contextMenuPatch: (LibraryContextMenu: any) => {
    outer?: Patch;
    inner?: Patch;
    unpatch: () => void;
};
/**
 * Game context menu component.
 */
export declare const LibraryContextMenu: any;
export default contextMenuPatch;
