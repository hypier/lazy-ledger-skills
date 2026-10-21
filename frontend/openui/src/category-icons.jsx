import React from 'react';

// Original 24×24 line drawings. Presentation only: these aliases never classify
// transactions or change a ledger's category hierarchy. No external SVG or URL.
export const categoryArtwork = {
  meal: 'M5 4v6m3-6v6M4 7h5v3a2.5 2.5 0 0 1-5 0V4m2.5 9v7M17 4c-3 3-3 6-3 9h4V4h-1m1 9v7',
  coffee: 'M4 8h12v7a4 4 0 0 1-4 4H8a4 4 0 0 1-4-4Zm12 1h2a3 3 0 0 1 0 6h-2M3 21h15M7 3v2m5-2v2',
  produce: 'M5 9h14l-2 11H7Zm3-2 3-4m2 4 3-4M3 9h18M10 12v5m4-5v5',
  snack: 'm7 3 2 1 3-1 3 1 2-1v18l-2-1-3 1-3-1-2 1ZM7 8h10M7 16h10m-7-4h4',
  transit: 'M6 4h12v13H6ZM6 11h12M9 7h6M8 20l2-3m4 0 2 3M9 14h.01M15 14h.01',
  fuel: 'M4 20V4h10v16M3 20h12M6 7h6v5H6Zm10-7 3 3v10a2 2 0 0 1-4 0v-3h-1m3-6h2',
  parking: 'M5 3h14v18H5Zm5 14V7h3a3 3 0 0 1 0 6h-3',
  maintenance: 'M14 4a5 5 0 0 0-5 7l-6 6 4 4 6-6a5 5 0 0 0 7-5l-3 2-3-3 2-4Z',
  shopping: 'M5 8h14l1 13H4ZM8 9V6a4 4 0 0 1 8 0v3',
  clothing: 'm8 4-5 4 3 4 2-2v11h8V10l2 2 3-4-5-4a4 4 0 0 1-8 0Z',
  supermarket: 'M3 4h2l3 12h10l3-8H6M9 20h.01M18 20h.01M10 11h7',
  household: 'M5 8h14v13H5ZM8 8V4h8v4M5 13h14M10 16h4',
  home: 'm3 11 9-8 9 8M5 10v11h14V10M10 21v-7h4v7',
  utilities: 'M5 21V3h14v18ZM8 7h2m4 0h2M8 11h2m4 0h2M10 21v-6h4v6',
  entertainment: 'M4 7h16v14H4ZM4 11h16M4 7l2-4h16l-2 4M10 3 8 7m8-4-2 4m-4 8 5 2-5 2Z',
  health: 'M4 6h16v15H4ZM9 6V3h6v3M12 10v7m-3-3.5h6',
  education: 'M12 6C9 3 5 4 3 5v15c3-2 6-2 9 0 3-2 6-2 9 0V5c-2-1-6-2-9 1Zm0 0v14',
  gift: 'M3 9h18v4H3Zm2 4v8h14v-8M12 9v12M12 9C2 9 6 0 10 5Zm0 0c10 0 6-9 2-4Z',
  envelope: 'M5 3h14v18H5ZM5 7l7 5 7-5M10 16h4',
  finance: 'M4 20V10h4v10m4 0V6h4v14m4 0V3M3 20h18M4 6l6-3',
  insurance: 'm12 3 8 3v6c0 4-5 8-8 10-3-2-8-6-8-10V6Zm-4 9 3 3 5-6',
  software: 'M3 4h18v14H3ZM8 21h8m-4-3v3M9 8l-3 3 3 3m6-6 3 3-3 3',
  income: 'M3 9h18v12H3ZM12 3v11m-3-3 3 3 3-3M6 17h3m6 0h3',
  salary: 'M4 8h16v13H4ZM8 8V4h8v4M4 13l8 3 8-3M10 14h4v3h-4Z',
  refund: 'M9 4 4 9l5 5M4 9h10a6 6 0 0 1 0 12h-3',
  transfer: 'M3 7h17l-4-4m4 14H3l4 4M20 7l-4 4M3 17l4-4',
  travel: 'M7 6h10v15H7ZM10 6V3h4v3M10 10v7m4-7v7M9 21v1m6-1v1',
  phone: 'M7 2h10v20H7ZM7 6h10M7 18h10M11 20h2',
  sport: 'M2 10v4m3-7v10m3-8v6m0-3h8m0-3v6m3-8v10m3-7v4',
  pet: 'M8 13c2-4 6-4 8 0l3 4c1 4-3 5-7 2-4 3-8 2-7-2Zm-3-9c-3 0-3 5 0 5s3-5 0-5m14 0c-3 0-3 5 0 5s3-5 0-5M10 2v4m4-4v4',
  tag: 'M4 4h8l9 9-8 8-9-9Zm4 4h.01',
};

export const categoryIconNames = {
  餐饮: 'meal', 外食: 'meal', 咖啡茶饮: 'coffee', 买菜: 'produce', 零食: 'snack',
  交通: 'transit', 加油: 'fuel', 停车通行: 'parking', 车辆养护: 'maintenance',
  购物: 'shopping', 服饰: 'clothing', 超市: 'supermarket', 日用百货: 'household', 其他购物: 'shopping',
  居住: 'home', 物业: 'utilities', 娱乐: 'entertainment', 医疗: 'health', 教育: 'education',
  人情: 'gift', 人情往来: 'gift', 红包: 'envelope', 红包收入: 'envelope',
  理财: 'finance', 保险: 'insurance', 数字服务: 'software', 软件服务: 'software',
  收入: 'income', 工资: 'salary', 奖金: 'income', 利息: 'finance', 报销: 'refund',
  退款: 'refund', 转账: 'transfer', 旅游: 'travel', 通讯: 'phone', 运动: 'sport', 宠物: 'pet',
  其他: 'tag', 未分类: 'tag',
};

export function categoryIconName(category) {
  const name = String(category ?? '').trim();
  return Object.hasOwn(categoryIconNames, name) ? categoryIconNames[name] : 'tag';
}

export function CategoryIcon({ category }) {
  const name = categoryIconName(category);
  return <svg className="category-icon" data-category-icon={name} viewBox="0 0 24 24"
    fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"
    aria-hidden="true" focusable="false"><path d={categoryArtwork[name]} /></svg>;
}

export function CategoryLabel({ category }) {
  return <span className="category-label"><CategoryIcon category={category} /><span>{category || '未分类'}</span></span>;
}
