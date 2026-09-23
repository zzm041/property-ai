// Presentation only; membership/validity comes from authenticated /me/houses.
function houseLabel(item) {
  return [item.property_name, item.building_name, item.unit_name, item.room_no]
    .filter(Boolean).join(" · ") || "房屋地址待完善";
}

module.exports = { houseLabel };
