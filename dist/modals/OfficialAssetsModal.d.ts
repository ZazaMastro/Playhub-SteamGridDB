import { FC } from 'react';
type FullAssetImages = {
    image: {
        [language: string]: string;
    };
    image2x: {
        [language: string]: string;
    };
};
type FullHeaderImages = {
    [languageCode: string]: string;
};
declare const OfficialAssetsModal: FC<{
    closeModal?: () => void;
    assetType: SGDBAssetType;
    onAssetChange?: (url: string) => Promise<any>;
    data: {
        steam: {
            id: string;
            metadata: {
                store_asset_mtime: number | null;
                library_capsule: string | null;
                library_logo: string | null;
                library_hero: string | null;
                steam_release_date: number | null;
                original_release_date: number | null;
                logo_position: string | null;
                header_image: string | null;
                clienticon: string | null;
                icon: string | null;
                header_image_full: FullHeaderImages;
                library_capsule_full: FullAssetImages;
                library_hero_full: FullAssetImages;
                library_logo_full: FullAssetImages;
            };
        }[];
    };
}>;
export default OfficialAssetsModal;
