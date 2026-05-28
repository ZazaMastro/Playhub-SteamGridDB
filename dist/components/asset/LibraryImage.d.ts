import { FC, CSSProperties } from 'react';
export interface LibraryImageProps {
    app?: AppStoreAppOverview;
    rgSources?: any;
    appid?: number;
    eAssetType: eAssetType;
    className?: string;
    imageClassName?: string;
    allowCustomization?: boolean;
    neverShowTitle?: boolean;
    name?: string;
    suppressTransitions?: boolean;
    bShortDisplay?: boolean;
    backgroundType?: 'transparent';
    onIncrementalError?: (evt: Event, t: any, r: any) => void;
    onLoad?: (evt: Event) => void;
    onError?: (evt: Event) => void;
    style?: CSSProperties;
}
declare const LibraryImage: FC<LibraryImageProps>;
export default LibraryImage;
