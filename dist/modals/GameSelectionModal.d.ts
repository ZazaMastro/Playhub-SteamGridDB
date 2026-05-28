import { FC } from 'react';
declare const GameSelectionModal: FC<{
    closeModal?: () => void;
    onSelect: (game: any) => void;
    searchGames: (term: string) => Promise<any[]>;
    defaultTerm: string;
}>;
export default GameSelectionModal;
