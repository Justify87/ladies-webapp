const EMPTY_GEOJSON=Object.freeze({type:'FeatureCollection',features:[]});

export function mapRefKey(value){
  const listingId=Number(value?.listing_id??value?.listingId??0);
  if(listingId>0)return `listing:${listingId}`;
  const advertId=String(value?.advert_id??value?.advertId??'').trim();
  return advertId?`advert:${advertId}`:'';
}

export function activeMapItems(results=[]){
  const items=[];
  const indexByListing=new Map();
  for(const row of results||[]){
    const listingId=Number(row?.listing_id??row?.listingId??0);
    if(listingId<=0)continue;
    const city=String(row?.city||'').trim();
    const existing=indexByListing.get(listingId);
    if(existing===undefined){
      indexByListing.set(listingId,items.length);
      items.push({listing_id:listingId,city});
    }else if(city&&!items[existing].city){
      items[existing].city=city;
    }
  }
  return items;
}

export function mapRequestDescriptor(state){
  const items=activeMapItems(state?.search?.results);
  const identity=items.map(item=>[item.listing_id,item.city.toLocaleLowerCase('de-DE')]);
  return {items,key:JSON.stringify(identity)};
}

function validCoordinatePair(value){
  if(!Array.isArray(value)||value.length<2)return false;
  const lng=Number(value[0]);
  const lat=Number(value[1]);
  return Number.isFinite(lng)&&Number.isFinite(lat)&&lng>=-180&&lng<=180&&lat>=-90&&lat<=90;
}

function normalizeListing(ref){
  if(!ref||typeof ref!=='object')return null;
  const listingId=Number(ref.listing_id||0);
  const advertId=String(ref.advert_id||'').trim();
  if(listingId<=0&&!advertId)return null;
  const rawPrice=ref.price_60_cents;
  const price=rawPrice===null||rawPrice===undefined||rawPrice===''?null:Number(rawPrice);
  return {
    person_id:Number(ref.person_id||0)||null,
    listing_id:listingId>0?listingId:null,
    advert_id:advertId,
    address_label:String(ref.address_label||''),
    price_60_cents:Number.isFinite(price)&&price>=0?price:null,
  };
}

function normalizeFeature(feature){
  if(feature?.geometry?.type!=='Point'||!validCoordinatePair(feature.geometry.coordinates))return null;
  const props=feature.properties||{};
  const key=String(props.location_key||'').trim();
  if(!key)return null;
  return {
    type:'Feature',
    geometry:{type:'Point',coordinates:[Number(feature.geometry.coordinates[0]),Number(feature.geometry.coordinates[1])]},
    properties:{
      ...props,
      location_key:key,
      listing_refs:(Array.isArray(props.listing_refs)?props.listing_refs:[]).map(normalizeListing).filter(Boolean),
    },
  };
}

export function sanitizeMapPoints(points){
  const rawFeatures=Array.isArray(points?.features)?points.features:[];
  const features=[];
  const invalidFeatures=[];
  for(const raw of rawFeatures){
    const feature=normalizeFeature(raw);
    if(feature)features.push(feature);
    else invalidFeatures.push(raw);
  }
  if(!invalidFeatures.length)return {points:points||{type:'FeatureCollection',features:[],meta:{}},invalidFeatures};

  const invalidListings=invalidFeatures.reduce((sum,feature)=>{
    const refs=feature?.properties?.listing_refs;
    return sum+(Array.isArray(refs)?refs.length:Number(feature?.properties?.listing_count||0));
  },0);
  const mappedListingCount=features.reduce((sum,feature)=>sum+feature.properties.listing_refs.length,0);
  const center=features.length?[
    features.reduce((sum,feature)=>sum+feature.geometry.coordinates[0],0)/features.length,
    features.reduce((sum,feature)=>sum+feature.geometry.coordinates[1],0)/features.length,
  ]:null;
  return {
    points:{
      ...(points||{}),
      type:'FeatureCollection',
      features,
      meta:{
        ...(points?.meta||{}),
        center,
        location_count:features.length,
        mapped_listing_count:mappedListingCount,
        unresolved_listing_count:Number(points?.meta?.unresolved_listing_count||0)+invalidListings,
        invalid_location_count:invalidFeatures.length,
      },
    },
    invalidFeatures,
  };
}

export function visibleListingSignature(rows=[]){
  const seen=new Set();
  const keys=[];
  for(const row of rows||[]){
    const key=mapRefKey(row);
    if(!key||seen.has(key))continue;
    seen.add(key);
    keys.push(key);
  }
  return keys.join('|');
}

export function indexMapRows(rows=[]){
  const index=new Map();
  for(const row of rows||[]){
    const key=mapRefKey(row);
    if(key&&!index.has(key))index.set(key,row);
  }
  return index;
}

function priceStats(refs){
  let sumCents=0;
  let count=0;
  for(const ref of refs){
    const cents=Number(ref?.price_60_cents);
    if(!Number.isFinite(cents)||cents<0)continue;
    sumCents+=cents;
    count+=1;
  }
  return {sumCents,count};
}

export function buildMapProjection(points,rows=[]){
  if(!points?.features?.length||!rows?.length){
    return {geojson:EMPTY_GEOJSON,locations:[],byKey:new Map(),signature:'empty'};
  }
  const visibleKeys=new Set(rows.map(mapRefKey).filter(Boolean));
  const features=[];
  const locations=[];
  const byKey=new Map();

  for(const sourceFeature of points.features){
    const sourceProps=sourceFeature.properties||{};
    const listings=(Array.isArray(sourceProps.listing_refs)?sourceProps.listing_refs:[])
      .filter(ref=>visibleKeys.has(mapRefKey(ref)));
    if(!listings.length)continue;
    const prices=priceStats(listings);
    const location={
      location_key:String(sourceProps.location_key||''),
      location_label:String(sourceProps.location_label||''),
      city:String(sourceProps.city||''),
      postal_code:String(sourceProps.postal_code||''),
      precision:String(sourceProps.precision||'SEARCH_AREA'),
      coordinates:sourceFeature.geometry.coordinates,
      listings,
    };
    locations.push(location);
    byKey.set(location.location_key,location);
    features.push({
      type:'Feature',
      geometry:{type:'Point',coordinates:location.coordinates},
      properties:{
        location_key:location.location_key,
        location_label:location.location_label,
        city:location.city,
        postal_code:location.postal_code,
        precision:location.precision,
        listing_count:listings.length,
        price_60_sum_cents:prices.sumCents,
        price_60_count:prices.count,
      },
    });
  }

  const signature=JSON.stringify(features.map(feature=>[
    feature.properties.location_key,
    feature.geometry.coordinates,
    feature.properties.location_label,
    feature.properties.precision,
    feature.properties.listing_count,
    feature.properties.price_60_sum_cents,
    feature.properties.price_60_count,
  ]));
  return {geojson:{type:'FeatureCollection',features},locations,byKey,signature};
}
