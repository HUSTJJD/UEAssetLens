#pragma once

#include "IAssetExtractor.h"

// ---------------------------------------------------------------------------
// FUALDomainFeatureExtractor: declarative M2 domain features (Priority 110).
//
// It runs after FUALTypedFeatureExtractor's vocabulary layer by calling that
// extractor first, so the frozen 16-key record and every existing feature.*
// key are always produced. It then reads only FAssetData metadata and adds
// domain.* keys to the existing DomainSpec MAP; no staging key, schema field,
// asset loading or raw metadata dump is introduced.
//
// All family, applicability, tag-fragment and typed-field rules come from the
// generated UALDomainProfiles.h. Structured values are emitted through
// UEYaml::EmitDocument from an FJsonObject DOM. Output has no clock values,
// host paths or generated identifiers.
// ---------------------------------------------------------------------------
class FUALDomainFeatureExtractor : public IAssetExtractor
{
public:
	virtual int32 Priority() const override { return 110; }
	virtual bool CanExtract(const FAssetData& AssetData) const override;
	virtual bool Extract(const FAssetData& AssetData, FUALAssetRecord& OutRecord) override;
};
