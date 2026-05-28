import { FC } from 'react';
declare const DropdownMultiselect: FC<{
    items: {
        label: string;
        value: any;
    }[];
    selected: any[];
    onSelect: (selected: any[]) => void;
    label: string;
}>;
export default DropdownMultiselect;
