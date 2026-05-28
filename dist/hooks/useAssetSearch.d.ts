import { FC, ReactNode } from 'react';
export type AssetSearchContextType = {
    loading: boolean;
    assets: any[];
    searchAndSetAssets: (assetType: SGDBAssetType, page: number, filters: any, onSuccess?: () => void) => Promise<void>;
    loadMore: (assetType: SGDBAssetType, onSuccess?: (res: any[]) => void) => Promise<void>;
    externalSgdbData: any;
    openFilters: (assetType: SGDBAssetType) => void;
    games: any[];
    selectedGame: any;
    isFilterActive: boolean;
    moreLoading: boolean;
    endReached: boolean;
};
export declare const SearchContext: import("react").Context<{}>;
export declare const AssetSearchContext: FC<{
    children: ReactNode;
}>;
export declare const useAssetSearch: () => AssetSearchContextType;
export default useAssetSearch;
