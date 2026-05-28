export interface Toolbar {
    assetType: SGDBAssetType;
    onSizeChange: (size: any) => void;
    onFilterClick: () => void;
    onOfficialAssetsClick: () => void;
    onLogoPosClick: () => void;
    disabled: boolean;
    noFocusRing?: boolean;
}
export type ToolbarRefType = {
    focus: () => void;
    assetSizeStyleAttr: any;
};
declare const Toolbar: import("react").ForwardRefExoticComponent<Toolbar & import("react").RefAttributes<ToolbarRefType>>;
export default Toolbar;
