import assert from "node:assert/strict";
import test from "node:test";
import {
  formatSeatCount,
  translateOptionCode,
  translateVehicleSpec,
} from "../app/vehicle-dictionary.ts";

test("Encar vehicle facts use the local RU/UA dictionary", () => {
  assert.equal(translateVehicleSpec("type", "중형차", "ru"), "Средний класс");
  assert.equal(translateVehicleSpec("type", "중형차", "uk"), "Середній клас");
  assert.equal(translateVehicleSpec("type", "쏘나타", "ru"), "쏘나타");
  assert.equal(translateVehicleSpec("fuel", "가솔린+전기", "ru"), "Бензин + электро");
  assert.equal(translateVehicleSpec("fuel", "LPG+전기", "uk"), "LPG + електро");
  assert.equal(translateVehicleSpec("color", "은회색", "uk"), "Сріблясто-сірий");
  assert.equal(translateVehicleSpec("transmission", "오토", "ru"), "Автоматическая");
  assert.equal(formatSeatCount(5, "uk"), "5 місць");
  assert.equal(formatSeatCount(1, "ru"), "1 место");
  assert.equal(translateOptionCode("058", "uk"), "Камера заднього виду");
  assert.equal(translateOptionCode("001", "ru"), "ABS");
  assert.equal(translateVehicleSpec("color", "새로운색", "ru"), "새로운색");
  assert.equal(translateOptionCode("999", "ru"), "Encar 999");
});
