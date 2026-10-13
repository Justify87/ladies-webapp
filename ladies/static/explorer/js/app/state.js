const config = window.__LADIES_CONFIG__ || {};
const defaults = config.defaults || {};

export const initialState = Object.freeze({
  config,
  navigation:{stack:[{key:'route-1',name:'search',params:{}}],seq:1},
  search:{
    filters:{city:defaults.city || 'Leipzig',radius:Number(defaults.radius_km || 20),category:'',ageMin:18,ageMax:45,heightMin:140,heightMax:200,selected:{geschlecht:['weiblich']},local:{photosOnly:true,videoOnly:false,likesOnly:false}},
    preview:{status:'idle',count:null,error:null}, recent:[], results:[], meta:null, loading:false, error:null
  },
  discover:{mode:'swipe',index:0,mediaIndex:{},history:[]},
  profiles:{bundles:{},details:{},loading:{}},
  lists:{tab:'FAVORITE',items:[],loading:false,statusCheck:{running:false,done:0,total:0,checkedAt:null}},
  dashboard:{counts:{},usage:{},recent_changes:[]},
  history:{kind:null,items:[]},
  settings:{values:{},storage:{},pricing:{},pricingRuntime:{},loaded:false},
  messaging:{status:{state:'UNKNOWN'},filter:'ALL',query:'',threads:[],loading:false,currentThread:null,threadLoading:false,replyTo:null,editing:null,eventsConnected:false,assignment:null,recording:false},
  pricing:{byThread:{},proposal:null},
  profileUi:{tabByPerson:{},mediaByPerson:{},expandedByPerson:{}},
  ui:{sheet:null,toast:null,busy:false},
  visual:{done:{},queued:{}}
});
