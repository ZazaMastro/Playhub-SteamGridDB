import { FC } from 'react';
declare const FiltersModal: FC<{
    closeModal?: () => void;
    assetType: SGDBAssetType;
    isNonsteam: boolean;
    onSave: (assetType: SGDBAssetType, filters: any, selectedGame?: any) => void;
    defaultFilters: any;
    defaultSelectedGame: any;
    defaultSearchTerm: string;
    searchGames: (term: string) => Promise<any[]>;
}>;
export default FiltersModal;
